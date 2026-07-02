"""
Dynamic daily keyword discovery — supplements the static seed list in research.py.

Sources:
1. eBay Browse API — most-watched / most-sold items per category
2. eBay Finding API — popular completedListings (high sold count)
3. CJ trending products — their own bestseller endpoint

Returns a list of (keyword, category, avg_price) tuples ready to pass into
the existing find_opportunities pipeline.
"""
import logging
import os
import re
import requests

log = logging.getLogger(__name__)

_CJ_EMAIL = os.getenv("CJ_EMAIL", "")
_CJ_KEY = os.getenv("CJ_API_KEY", "")

_EBAY_APP_ID = os.getenv("EBAY_APP_ID", "")

# eBay category IDs to scan for trending items (UK GB categories)
_EBAY_SCAN_CATEGORIES = {
    "health_beauty": ("26395", "q=beauty"),
    "home_garden": ("11700", "q=home+decor"),
    "tech_accessories": ("58058", "q=gadgets"),
    "pet_supplies": ("1281", "q=pet"),
    "sports_leisure": ("888", "q=fitness"),
    "baby_kids": ("2", "q=baby"),
    "office_stationery": ("26395", "q=office"),
}

# Stop words to clean up eBay titles into keywords
_STOPWORDS = {
    "with", "for", "and", "the", "new", "set", "pack", "pcs", "lot",
    "uk", "free", "fast", "ship", "delivery", "quality", "best",
    "hot", "sale", "item", "good", "nice", "buy", "cheap", "great",
    "high", "low", "top", "pro", "plus", "mini", "max", "auto",
}


def _clean_title(title: str, max_words: int = 4) -> str:
    """Extract core keyword from an eBay listing title."""
    words = re.sub(r"[^a-zA-Z0-9 ]", " ", title.lower()).split()
    core = [w for w in words if w not in _STOPWORDS and len(w) >= 4]
    return " ".join(core[:max_words])


def fetch_ebay_trending(max_per_category: int = 5) -> list[dict]:
    """
    Pull best-match active listings from eBay Browse API per category.
    Returns list of {keyword, category, avg_price_gbp}
    """
    from config import get_app_token, EBAY_BROWSE_BASE
    results = []

    for cat_name, (cat_id, q_hint) in _EBAY_SCAN_CATEGORIES.items():
        try:
            headers = {
                "Authorization": f"Bearer {get_app_token()}",
                "X-EBAY-C-MARKETPLACE-ID": "EBAY_GB",
                "X-EBAY-C-ENDUSERCTX": "contextualLocation=country%3DGB",
            }
            resp = requests.get(
                f"{EBAY_BROWSE_BASE}/item_summary/search",
                headers=headers,
                params={
                    "category_ids": cat_id,
                    "sort": "bestMatch",
                    "limit": 10,
                    "filter": "price:[8..250],buyingOptions:{FIXED_PRICE},conditionIds:{1000}",
                },
                timeout=12,
            )
            items = resp.json().get("itemSummaries", [])
            seen = set()
            for item in items[:max_per_category]:
                title = item.get("title", "")
                price = float(item.get("price", {}).get("value", 0))
                if price < 8:
                    continue
                kw = _clean_title(title)
                if not kw or kw in seen:
                    continue
                seen.add(kw)
                results.append({
                    "keyword": kw,
                    "category": cat_name,
                    "avg_price_gbp": price,
                    "source": "ebay_trending",
                })
                log.info(f"eBay trending [{cat_name}]: '{kw}' @ £{price:.2f}")

        except Exception as e:
            log.warning(f"eBay trending fetch failed for {cat_name}: {e}")

    return results


def fetch_cj_trending(max_results: int = 20) -> list[dict]:
    """Pull CJ's own trending/hot products for GB."""
    results = []
    try:
        # CJ auth: email is account email, password field takes the API key
        api_key_parts = _CJ_KEY.split("@api@")
        actual_pass = api_key_parts[1] if len(api_key_parts) > 1 else _CJ_KEY
        token_resp = requests.post(
            "https://developers.cjdropshipping.com/api2.0/v1/authentication/getAccessToken",
            json={"email": _CJ_EMAIL, "password": actual_pass},
            timeout=10,
        )
        body = token_resp.json()
        token = (body.get("data") or {}).get("accessToken", "")
        if not token:
            log.warning(f"CJ trending: no token — {body.get('message', '')}")
            return []

        resp = requests.get(
            "https://developers.cjdropshipping.com/api2.0/v1/product/list",
            headers={"CJ-Access-Token": token},
            params={
                "countryCode": "GB",
                "pageNum": 1,
                "pageSize": max_results,
                "sortType": "HOT_SELL",
            },
            timeout=15,
        )
        items = (resp.json().get("data") or {}).get("list") or []
        for item in items:
            name = item.get("productNameEn", "")
            price_raw = item.get("sellPrice") or item.get("productPrice") or 0
            price = float(price_raw)
            if not name or price < 2:
                continue
            kw = _clean_title(name, max_words=3)
            if not kw:
                continue
            results.append({
                "keyword": kw,
                "category": "tech_accessories",
                "avg_price_gbp": round(price * 2.8, 2),
                "source": "cj_trending",
            })
        log.info(f"CJ trending: {len(results)} products found")
    except Exception as e:
        log.warning(f"CJ trending fetch failed: {e}")

    return results


def get_fresh_keywords(max_total: int = 40) -> list[dict]:
    """Main entry — returns deduplicated list of fresh keyword opportunities."""
    seen = set()
    all_kws = []

    for item in fetch_ebay_trending(max_per_category=5):
        if item["keyword"] not in seen:
            seen.add(item["keyword"])
            all_kws.append(item)

    for item in fetch_cj_trending(max_results=20):
        if item["keyword"] not in seen:
            seen.add(item["keyword"])
            all_kws.append(item)

    log.info(f"Fresh keyword discovery: {len(all_kws)} unique keywords found")
    return all_kws[:max_total]


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    kws = get_fresh_keywords()
    print(f"\nFound {len(kws)} fresh keywords:")
    for k in kws:
        print(f"  [{k['category']}] {k['keyword']} @ £{k['avg_price_gbp']:.2f} (via {k['source']})")
