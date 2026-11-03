"""
Self-directed campaign discovery. Given a THEME (e.g. the UK government 'Be Ready' campaign) the agent
works out for itself what people will buy, instead of relying on a supplied keyword list:

  1. BRAINSTORM  - Claude lists every single-item, Amazon-UK-sourceable product the theme could drive
                   (obvious checklist items, adjacent needs, second-order demand, seasonal tie-ins).
  2. MINE        - searches eBay UK for theme queries, reads real sold counts (estimatedSoldQuantity) of the
                   top results, and has Claude distil the best-selling listings into product keywords.
  3. RESEARCH    - the normal research gates (sold/week, competition, price band) via find_opportunities().
  4. LIST        - the normal listing pipeline (Amazon match, stock gate, competitive pricing, promotion).

Usage:  python campaign_discover.py "<theme>" [--dry] [--cap N] [--skip kw1,kw2]
Run under: systemd-run --scope -p MemoryMax=1000M venv/bin/python campaign_discover.py ...
"""
import json
import logging
import os
import re
import sys

os.environ["QUOTA_RESERVE"] = "250"   # campaign may dip deeper than the agent default (600)
os.environ["SEASONAL_ONLY"] = "0"   # campaign runs list outside the Christmas-only seasonal filter
from dotenv import load_dotenv

load_dotenv()
log = logging.getLogger("campaign_discover")

MODEL = "claude-sonnet-4-6"
CATS = ["tools_hardware", "auto_parts", "sports_fitness", "health_devices", "garden_outdoor", "home_living",
        "home_appliances", "tech_accessories", "pet_supplies", "baby_kids", "office_stationery"]


def _claude_json(prompt: str, max_tokens: int = 4000):
    import anthropic
    from config import ANTHROPIC_API_KEY
    msg = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY).messages.create(
        model=MODEL, max_tokens=max_tokens, messages=[{"role": "user", "content": prompt}])
    text = msg.content[0].text
    m = re.search(r"\{.*\}", text, re.S)
    return json.loads(m.group(0)) if m else {}


def brainstorm(theme: str) -> dict:
    prompt = f"""You are a UK eBay dropshipping strategist. Theme driving shopper demand RIGHT NOW: {theme}

List 120-160 distinct SINGLE products (not kits/bundles) that UK shoppers will search eBay for because of this
theme. Think widely and creatively, not just the obvious checklist: direct needs, adjacent needs, second-order
effects (cold homes, no power/cooking/heating/comms, cash/payments, water, food prep without power, safety,
car/travel resilience, home security, flood/storm, medical, pets, babies, elderly), seasonal tie-ins (it is
November, winter coming), and items people overlook (manual tin openers, thermos flasks, hot water bottles,
draught excluders, candles+holders, matches, battery storage, USB-C cables, whistles, dry bags, etc).
Rules: each keyword is a 2-5 word eBay search phrase for ONE product type that Amazon UK sells for roughly
GBP 4-80. NO food, drink, medicine, gas canisters, loose lithium batteries, weapons, or branded products.
Assign each to one category key from: {CATS}.
Return ONLY JSON: {{"category_key": ["keyword", ...], ...}}"""
    return _claude_json(prompt, 6000)


def mine_ebay(seeds: list, per_seed: int = 12) -> list:
    """Search eBay for theme queries and return the best sellers by real sold count."""
    import requests
    from research import _browse_headers
    from config import EBAY_BROWSE_BASE
    found = {}
    for q in seeds:
        try:
            r = requests.get(f"{EBAY_BROWSE_BASE}/item_summary/search", headers=_browse_headers(), timeout=15, params={
                "q": q, "limit": 30, "sort": "bestMatch",
                "filter": "price:[3..120],priceCurrency:GBP,buyingOptions:{FIXED_PRICE},deliveryCountry:GB"})
            items = r.json().get("itemSummaries", [])[:per_seed]
        except Exception as e:
            log.warning(f"mine '{q}': {e}")
            continue
        for it in items:
            iid = it.get("itemId")
            if not iid or iid in found:
                continue
            try:
                d = requests.get(f"{EBAY_BROWSE_BASE}/item/{iid}", headers=_browse_headers(), timeout=15).json()
                sold = (d.get("estimatedAvailabilities") or [{}])[0].get("estimatedSoldQuantity") or 0
            except Exception:
                continue
            found[iid] = {"title": it.get("title", ""), "sold": sold, "price": it.get("price", {}).get("value"), "q": q}
    return sorted(found.values(), key=lambda x: x["sold"], reverse=True)


def distil(theme: str, best: list) -> dict:
    lines = "\n".join(f"- {b['title']} | sold {b['sold']} | GBP {b['price']}" for b in best[:70])
    prompt = f"""Theme: {theme}. These are the best-selling live eBay UK listings found for theme-related searches:
{lines}

Turn them into 30-60 generic eBay search keywords (2-5 words, ONE product type each, no brand names, no food/
drink/medicine/gas canisters) for products we could source on Amazon UK. Include products you spot that are
clearly selling but are NOT obvious for the theme. Categories allowed: {CATS}.
Return ONLY JSON: {{"category_key": ["keyword", ...]}}"""
    return _claude_json(prompt, 3000)


def merge(*dicts, skip=()):
    skip = {s.strip().lower() for s in skip}
    out, seen = {}, set()
    for d in dicts:
        for cat, kws in d.items():
            if cat not in CATS:
                cat = "home_living"
            for kw in kws:
                k = kw.strip().lower()
                if k and k not in seen and k not in skip:
                    seen.add(k)
                    out.setdefault(cat, []).append(kw.strip())
    return out


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s — %(message)s")
    theme = sys.argv[1]
    dry = "--dry" in sys.argv
    cap = int(sys.argv[sys.argv.index("--cap") + 1]) if "--cap" in sys.argv else 40
    skip = sys.argv[sys.argv.index("--skip") + 1].split(",") if "--skip" in sys.argv else []

    try:
        brain = brainstorm(theme)
    except Exception as e:                     # e.g. Anthropic credits exhausted - carry on with the other sources
        log.warning(f"Claude brainstorm failed ({str(e)[:100]}) - using hand-written + mined keywords only")
        brain = {}
    manual = {}
    mf = os.path.join(os.path.dirname(os.path.abspath(__file__)), "campaign_keywords_manual.json")
    if os.path.exists(mf):
        with open(mf, encoding="utf-8") as f:
            manual = {k: v for k, v in json.load(f).items() if not k.startswith("_")}
    log.info(f"Brainstormed {sum(len(v) for v in brain.values())} (Claude) + {sum(len(v) for v in manual.values())} (manual) keywords")
    seeds = ["power cut essentials", "emergency preparedness", "prepper survival", "blackout winter",
             "camping essentials", "storm flood protection", "wind up", "solar powered", "thermal keep warm",
             "off grid cooking", "emergency lighting", "winter car breakdown", "cash storage safe",
             "water storage", "home safety fire"]
    best = mine_ebay(seeds)
    log.info(f"Mined {len(best)} live listings; top sellers: " +
             "; ".join(f"{b['title'][:40]} ({b['sold']})" for b in best[:8]))
    try:
        mined = distil(theme, best) if best else {}
    except Exception as e:
        log.warning(f"Claude distil failed ({str(e)[:100]}) - skipping mined keywords")
        mined = {}
    kws = merge(manual, brain, mined, skip=skip)
    with open("campaign_keywords.json", "w", encoding="utf-8") as f:
        json.dump({"brainstorm": brain, "mined": mined, "top_sellers": best[:70]}, f, indent=1)
    log.info(f"Researching {sum(len(v) for v in kws.values())} unique keywords")

    from research import find_opportunities
    import main
    opps = find_opportunities(top_n=cap * 3, only_keywords=kws)
    print(f"\n{len(opps)} qualifying keyword(s):")
    for o in opps:
        print(f"  [{o['category']}] {o['keyword']}: £{o['avg_price_gbp']} | {o['active_listings']} competitors | {o['sold_per_week']}/wk")
    main.research_and_list(dry_run=dry, opportunities=opps, cap=cap)
