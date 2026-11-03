"""
Worker for the Telegram listener. Runs as its own process (under a memory-limited systemd scope) because it
drives Playwright. Writes its answer as JSON to the path given as the last argument.

  python sourcing_job.py find <image_path> <caption> <out.json>   screenshot -> product -> Amazon match -> price preview
  python sourcing_job.py plan <asin_or_url> <caption> <out.json>   known Amazon product -> price preview
  python sourcing_job.py list <asin> <keyword> <category> <price> <out.json>   create the eBay listing
"""
import base64
import json
import logging
import mimetypes
import os
import re
import sys

os.environ.setdefault("SEASONAL_ONLY", "0")
from dotenv import load_dotenv

load_dotenv()
log = logging.getLogger("sourcing_job")

VISION_MODEL = "claude-sonnet-4-6"
FAST_MODEL = "claude-haiku-4-5"
CATS = ["tools_hardware", "auto_parts", "sports_fitness", "health_devices", "garden_outdoor", "home_living",
        "home_appliances", "tech_accessories", "pet_supplies", "baby_kids", "office_stationery", "seasonal"]


def _claude():
    import anthropic
    from config import ANTHROPIC_API_KEY
    return anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)


def _json(text: str) -> dict:
    m = re.search(r"\{.*\}", text, re.S)
    return json.loads(m.group(0)) if m else {}


def read_screenshot(path: str, caption: str) -> dict:
    mime = mimetypes.guess_type(path)[0] or "image/jpeg"
    data = base64.standard_b64encode(open(path, "rb").read()).decode()
    prompt = f"""This screenshot shows a product a UK eBay seller wants to source from Amazon UK and resell.
Seller's note: "{caption}"
Identify the SINGLE product pictured. Return ONLY JSON:
{{"product": "full product name as shown", "brand": "", "model": "", "pack_size": "e.g. 1, 2-pack, 92 pieces",
 "visible_price_gbp": null, "search_queries": ["3 different Amazon UK search phrases, most specific first"],
 "ebay_keyword": "2-5 word generic eBay search phrase for this product type (no brand)",
 "category_key": "one of {CATS}", "notes": "anything unclear or risky (brand, restricted item, multiple products)"}}"""
    msg = _claude().messages.create(model=VISION_MODEL, max_tokens=700, messages=[{"role": "user", "content": [
        {"type": "image", "source": {"type": "base64", "media_type": mime, "data": data}},
        {"type": "text", "text": prompt}]}])
    return _json(msg.content[0].text)


def derive_from_title(title: str, caption: str) -> dict:
    prompt = f"""Amazon UK product: "{title}". Seller note: "{caption}".
Return ONLY JSON: {{"ebay_keyword": "2-5 word generic eBay search phrase for this product type (no brand)",
"category_key": "one of {CATS}"}}"""
    msg = _claude().messages.create(model=FAST_MODEL, max_tokens=150, messages=[{"role": "user", "content": prompt}])
    return _json(msg.content[0].text)


def pick_best(spec: dict, cands: list) -> dict:
    lines = "\n".join(f"{i}. {c['title'][:140]} | GBP {c['cost_gbp']}" for i, c in enumerate(cands))
    prompt = f"""Wanted product: {json.dumps(spec)}
Amazon UK candidates:
{lines}
Which candidate is the SAME product (same brand/model, same pack size/count)? Return ONLY JSON:
{{"best_index": <number or null if none is the same product>, "confidence": "high|medium|low", "reason": "one short sentence"}}"""
    msg = _claude().messages.create(model=FAST_MODEL, max_tokens=200, messages=[{"role": "user", "content": prompt}])
    return _json(msg.content[0].text)


def preview(plan: dict) -> dict:
    """Turn seed_amazon.prepare() output into numbers for the Telegram preview."""
    from config import calculate_ebay_fees, total_cost_with_vat
    prod, target, fee_key = plan["prod"], plan["target"], plan["fee_key"]
    cost = total_cost_with_vat(prod["cost_gbp"], prod["shipping_gbp"])
    fees = calculate_ebay_fees(target, fee_key)
    if os.getenv("PROMOTE_CAMPAIGN_ID"):
        fees += target * float(os.getenv("PROMOTE_RATE", "2.0")) / 100 * 1.2
    return {"asin": plan["asin"], "title": prod["title"], "amazon_shelf": plan["shelf"], "cost_total": round(cost, 2),
            "ebay_price": round(target, 2), "est_profit": round(target - cost - fees, 2),
            "market": plan["market"], "competitors": plan["active"], "keyword": plan["keyword"],
            "category": plan["category"], "image": (prod.get("images") or [""])[0]}


def do_plan(asin_or_url: str, keyword: str, category: str):
    from seed_amazon import prepare, SeedSkip
    try:
        return {"ok": True, "preview": preview(prepare(asin_or_url, keyword, category))}
    except SeedSkip as e:
        msg = str(e)
        if "no eBay market data" in msg:
            msg += " (eBay API may be rate limited - try again later)"
        return {"ok": False, "error": msg}


def main():
    mode = sys.argv[1]
    out = sys.argv[-1]
    from db import init_db
    init_db()
    import amazon_source
    try:
        if mode == "find":
            spec = read_screenshot(sys.argv[2], sys.argv[3])
            cands, seen = [], set()
            cap = max(float(spec.get("visible_price_gbp") or 0) * 1.6, 60.0)
            for q in (spec.get("search_queries") or [spec.get("product", "")])[:3]:
                for c in amazon_source.search_amazon(q, cap, limit=5, strict=False, min_reviews=0):
                    if c.get("product_id") and c["product_id"] not in seen:
                        seen.add(c["product_id"])
                        cands.append(c)
                if len(cands) >= 8:
                    break
            if not cands:
                res = {"ok": False, "spec": spec, "error": "Nothing found on Amazon UK for that product."}
            else:
                cands = cands[:8]
                pick = pick_best(spec, cands)
                alts = [{"asin": c["product_id"], "title": c["title"][:90], "price": c["cost_gbp"]} for c in cands[:4]]
                idx = pick.get("best_index")
                if idx is None or not (0 <= int(idx) < len(cands)):
                    res = {"ok": False, "spec": spec, "alternatives": alts,
                           "error": "No exact match found. " + pick.get("reason", "")}
                else:
                    best = cands[int(idx)]
                    res = do_plan(best["product_id"], spec.get("ebay_keyword") or spec.get("product", ""),
                                  spec.get("category_key", "home_living"))
                    res.update({"spec": spec, "confidence": pick.get("confidence"), "reason": pick.get("reason"),
                                "alternatives": [a for a in alts if a["asin"] != best["product_id"]][:3]})
        elif mode == "plan":
            first = amazon_source.get_product(re.search(r"([A-Z0-9]{10})", sys.argv[2]).group(1))
            d = derive_from_title(first["title"], sys.argv[3]) if first else {}
            res = do_plan(sys.argv[2], d.get("ebay_keyword") or (first or {}).get("title", "")[:40],
                          d.get("category_key", "home_living"))
        elif mode == "list":
            from seed_amazon import seed
            asin, keyword, cat, price = sys.argv[2], sys.argv[3], sys.argv[4], float(sys.argv[5])
            ok = seed(asin, keyword, cat, dry_run=False, price=price)
            lid = None
            if ok:
                import sqlite3
                from db import DB_PATH
                row = sqlite3.connect(DB_PATH).execute(
                    "SELECT ebay_listing_id FROM products WHERE supplier='amazon' AND supplier_product_id=? "
                    "ORDER BY id DESC LIMIT 1", (asin,)).fetchone()
                lid = row[0] if row else None
            res = {"ok": bool(ok), "listing_id": lid,
                   "error": None if ok else "Listing failed - check the agent log (ebay_agent.log)."}
        else:
            res = {"ok": False, "error": f"unknown mode {mode}"}
    except Exception as e:
        log.exception("job failed")
        res = {"ok": False, "error": f"{type(e).__name__}: {str(e)[:200]}"}
    finally:
        try:
            amazon_source.close()
        except Exception:
            pass
    with open(out, "w", encoding="utf-8") as f:
        json.dump(res, f)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s - %(message)s")
    main()
