"""
Self-directed campaign discovery. Given a THEME (e.g. the UK government 'Be Ready' campaign) the agent
works out for itself what people will buy, instead of relying on a supplied keyword list:

  1. BRAINSTORM  - Claude lists every single-item, Amazon-UK-sourceable product the theme could drive
                   (obvious checklist items, adjacent needs, second-order demand, seasonal tie-ins).
  2. MINE        - searches eBay UK for theme queries, reads real sold counts (estimatedSoldQuantity) of the
                   top results, and has Claude distil the best-selling listings into product keywords.
  3. RESEARCH    - the normal research gates (sold/week, competition, price band) via find_opportunities().
  4. LIST        - the normal listing pipeline (Amazon match, stock gate, competitive pricing, promotion).

Usage:  python campaign_discover.py "<theme>" [--dry] [--cap N] [--skip kw1,kw2] [--seeds "q1,q2"] [--manual file.json]
The brainstorm is also told what has already SOLD for us (orders table) so it finds siblings of winners.
Run under: systemd-run --scope -p MemoryMax=1000M venv/bin/python campaign_discover.py ...
"""
import json
import logging
import os
import re
import sys

os.environ["QUOTA_RESERVE"] = "250"   # campaign may dip deeper than the agent default (600)
os.environ["SEASONAL_ONLY"] = "0"   # campaign runs list outside the Christmas-only seasonal filter
os.environ.setdefault("DAILY_LISTING_CAP", "150")   # set before config loads .env (which never overrides)
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


def winners_context(limit: int = 12) -> str:
    """Titles of products that have actually sold (last 90 days) - the strongest signal of what works for us."""
    try:
        import sqlite3
        from db import DB_PATH
        conn = sqlite3.connect(DB_PATH)
        rows = conn.execute("""SELECT p.title, p.category, o.sale_price_gbp, o.profit_gbp FROM orders o
                               JOIN products p ON p.id = o.product_id
                               WHERE o.status != 'cancelled' AND o.created_at >= date('now','-90 day')
                               ORDER BY o.id DESC LIMIT ?""", (limit,)).fetchall()
        conn.close()
        return "\n".join(f"- {t[:70]} [{c}] sold GBP {sp:.2f}, profit GBP {pf:.2f}" for t, c, sp, pf in rows)
    except Exception as e:
        log.debug(f"winners_context failed: {e}")
        return ""


def seed_queries(theme: str) -> list:
    """eBay search phrases to mine best-sellers for this theme."""
    try:
        d = _claude_json(f"""Theme: {theme}
Give 14 short eBay UK search phrases (2-4 words each) a shopper would use for this theme, covering different
product areas. Return ONLY JSON: {{"queries": ["...", ...]}}""", 600)
        q = [x for x in d.get("queries", []) if isinstance(x, str)]
        if q:
            return q[:16]
    except Exception as e:
        log.warning(f"seed_queries failed ({str(e)[:80]})")
    return [w for w in re.split(r"[,:;()]", theme) if len(w.strip()) > 3][:8]


def brainstorm(theme: str) -> dict:
    win = winners_context()
    prompt = f"""You are a UK eBay dropshipping strategist. Theme driving shopper demand RIGHT NOW: {theme}

Products that have ALREADY SOLD for this seller recently (find siblings/variants of the winning TYPES):
{win or '(none yet)'}

List 120-170 distinct products that UK shoppers will search eBay for because of this theme. Think widely and
creatively, not just the obvious: direct needs, adjacent needs, second-order effects, gifting angles, seasonal
tie-ins (it is early November in the UK, Christmas and winter ahead), and items people overlook. Include
ready-made KITS and BUNDLES where a kit is what people actually buy (e.g. survival / emergency / car kits sold
well at GBP 60), as well as single items.
Rules: each keyword is a 2-5 word eBay search phrase for ONE product type that Amazon UK sells for roughly
GBP 4-90. NO food, drink, medicine, gas canisters, loose lithium batteries, weapons, knives, or branded
products. Assign each to one category key from: {CATS}.
Return ONLY JSON: {{"category_key": ["keyword", ...], ...}}"""
    return _claude_json(prompt, 6500)


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
    if "--manual" in sys.argv:
        mf = sys.argv[sys.argv.index("--manual") + 1]
        if os.path.exists(mf):
            with open(mf, encoding="utf-8") as f:
                manual = {k: v for k, v in json.load(f).items() if not k.startswith("_")}
    log.info(f"Brainstormed {sum(len(v) for v in brain.values())} (Claude) + {sum(len(v) for v in manual.values())} (manual) keywords")
    seeds = sys.argv[sys.argv.index("--seeds") + 1].split(",") if "--seeds" in sys.argv else seed_queries(theme)
    log.info(f"Mining eBay with seeds: {seeds}")
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
