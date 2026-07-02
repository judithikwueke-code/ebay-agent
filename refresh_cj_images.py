"""
One-off script: refresh images on all active CJ eBay listings.

We stored the variant `vid` in products.supplier_product_id, but the CJ
detail API (/v1/product/query) requires the parent `pid`. This script
re-discovers the pid by searching CJ with title keywords, then queries
the full product detail for the complete image set and PUTs the eBay
inventory item with all images.
"""

import hashlib
import logging
import re
import sqlite3
import time
import requests
from config import EBAY_SELL_BASE, get_user_token
from supplier import _get_cj_access_token

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-8s — %(message)s")
log = logging.getLogger(__name__)

DB_PATH = "ebay_agent.db"

_SKIP = {
    "with", "and", "for", "the", "in", "of", "to", "a", "an", "usb", "led",
    "uk", "set", "pro", "new", "mini", "plus", "type", "fast", "dual", "multi",
    "4", "2", "1", "8", "12", "3", "5", "cordless", "portable", "wireless",
    "electric", "handheld", "heated", "digital", "channel", "modes", "relief",
    "compression", "vibration", "airbag", "bluetooth", "music", "blower",
    "duster", "cleaner", "speaker", "holder",
}


def _keywords_from_title(title: str) -> str:
    """Extract 3 meaningful product-specific words from a Claude-generated title."""
    words = re.sub(r"[^a-zA-Z0-9 ]", " ", title).split()
    core = [w for w in words if w.lower() not in _SKIP and len(w) >= 4]
    return " ".join(core[:3])


def _sell_headers() -> dict:
    return {
        "Authorization": f"Bearer {get_user_token()}",
        "X-EBAY-C-MARKETPLACE-ID": "EBAY_GB",
        "Content-Language": "en-GB",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }


def _cj_headers() -> dict:
    token = _get_cj_access_token()
    return {"CJ-Access-Token": token, "Content-Type": "application/json"}


def _cj_search_for_pid(keyword: str, target_vid: str) -> str | None:
    """
    Search CJ by keyword, then check each result's variants for a matching vid.
    Returns the pid whose cheapest variant matches target_vid.
    Tries with GB warehouse filter first; falls back to global search so the
    vid match isn't blocked by warehouse availability changing after listing.
    """
    headers = _cj_headers()

    def _do_search(params):
        try:
            resp = requests.get(
                "https://developers.cjdropshipping.com/api2.0/v1/product/list",
                headers=headers, params=params, timeout=15,
            )
            if resp.status_code == 429:
                log.info("CJ rate limit — waiting 30s")
                time.sleep(30)
                resp = requests.get(
                    "https://developers.cjdropshipping.com/api2.0/v1/product/list",
                    headers=headers, params=params, timeout=15,
                )
            resp.raise_for_status()
            return resp.json().get("data", {}).get("list", [])
        except Exception as e:
            log.warning(f"CJ search failed for '{keyword}': {e}")
            return []

    base = {"productNameEn": keyword, "pageNum": 1, "pageSize": 20}
    products = _do_search({**base, "countryCode": "GB"})
    if not products:
        log.debug(f"  No GB results for '{keyword}' — retrying without country filter")
        products = _do_search(base)

    for product in products:
        pid = str(product.get("pid", ""))
        if not pid:
            continue
        time.sleep(0.5)
        try:
            detail_resp = requests.get(
                "https://developers.cjdropshipping.com/api2.0/v1/product/query",
                headers=_cj_headers(),
                params={"pid": pid},
                timeout=15,
            )
            if detail_resp.status_code == 429:
                time.sleep(30)
                detail_resp = requests.get(
                    "https://developers.cjdropshipping.com/api2.0/v1/product/query",
                    headers=_cj_headers(), params={"pid": pid}, timeout=15,
                )
            detail_resp.raise_for_status()
            data = detail_resp.json().get("data", {})
            for v in data.get("variants", []):
                if str(v.get("vid", "")) == target_vid:
                    return pid
        except Exception as e:
            log.debug(f"Variant check failed for pid {pid}: {e}")
            continue

    return None


def _parse_img(val) -> list[str]:
    """Normalise CJ image fields — handles plain URL or JSON-encoded array string."""
    import json as _j
    if not val:
        return []
    if isinstance(val, list):
        out = []
        for v in val:
            out.extend(_parse_img(v))
        return out
    if not isinstance(val, str):
        return []
    val = val.strip()
    if val.startswith("["):
        try:
            parsed = _j.loads(val)
            if isinstance(parsed, list):
                return [u for u in parsed if isinstance(u, str) and u.startswith("http")]
        except Exception:
            pass
    return [val] if val.startswith("http") else []


def fetch_images_by_pid(pid: str) -> list[str]:
    """Fetch all product images from CJ for a given pid."""
    headers = _cj_headers()
    try:
        resp = requests.get(
            "https://developers.cjdropshipping.com/api2.0/v1/product/query",
            headers=headers, params={"pid": pid}, timeout=15,
        )
        if resp.status_code == 429:
            time.sleep(30)
            resp = requests.get(
                "https://developers.cjdropshipping.com/api2.0/v1/product/query",
                headers=headers, params={"pid": pid}, timeout=15,
            )
        resp.raise_for_status()
        data = resp.json().get("data", {})
        seen = set()
        imgs = []
        def _add(val):
            for u in _parse_img(val):
                if u not in seen:
                    seen.add(u)
                    imgs.append(u)
        # Variant images first (distinct product shots for each size/colour)
        for v in data.get("variants", []):
            _add(v.get("variantImage", ""))
            if len(imgs) >= 3:
                break
        # Full product image gallery
        for img in data.get("productImages", []):
            if isinstance(img, dict):
                _add(img.get("imageUrl") or img.get("url") or "")
            else:
                _add(img)
        _add(data.get("productImage", ""))
        return imgs[:8]
    except Exception as e:
        log.warning(f"CJ detail fetch failed for pid {pid}: {e}")
        return []


def get_ebay_item(sku: str) -> dict:
    url = f"{EBAY_SELL_BASE}/inventory_item/{sku}"
    try:
        resp = requests.get(url, headers=_sell_headers(), timeout=15)
        if resp.status_code == 200:
            return resp.json()
    except Exception as e:
        log.warning(f"eBay GET failed for {sku}: {e}")
    return {}


def put_ebay_images(sku: str, images: list[str], existing: dict) -> bool:
    url = f"{EBAY_SELL_BASE}/inventory_item/{sku}"
    ep = existing.get("product", {})
    # Build a clean PUT payload — only fields eBay's PUT endpoint accepts.
    # Passing the raw GET response body causes error 25709 (invalid Content-Language)
    # because GET returns extra read-only fields the PUT rejects.
    payload = {
        "availability": {
            "shipToLocationAvailability": {
                "quantity": (existing.get("availability", {})
                             .get("shipToLocationAvailability", {})
                             .get("quantity", 5))
            }
        },
        "condition": existing.get("condition", "NEW"),
        "product": {
            "title": ep.get("title", ""),
            "description": ep.get("description", ""),
            "imageUrls": [img for img in images if img][:6],
            "aspects": ep.get("aspects", {}),
        },
    }
    try:
        resp = requests.put(url, headers=_sell_headers(), json=payload, timeout=15)
        if resp.status_code not in (200, 201, 204):
            log.warning(f"  eBay PUT {resp.status_code}: {resp.text[:200]}")
            return False
        return True
    except Exception as e:
        log.warning(f"eBay PUT failed for {sku}: {e}")
        return False


def main():
    conn = sqlite3.connect(DB_PATH)
    rows = conn.execute(
        "SELECT ebay_listing_id, supplier_product_id, title FROM products "
        "WHERE supplier='cj' AND status='active'"
    ).fetchall()
    conn.close()

    log.info(f"Found {len(rows)} active CJ listings")

    for ebay_id, vid, title in rows:
        sku = hashlib.md5(f"cj-{vid}".encode()).hexdigest()[:20]
        log.info(f"Processing: {title[:60]}")

        existing = get_ebay_item(sku)
        if not existing:
            log.warning("  Could not fetch eBay item — skipping")
            continue
        current_imgs = existing.get("product", {}).get("imageUrls", [])
        if len(current_imgs) >= 4:
            log.info(f"  Already has {len(current_imgs)} images — skipping")
            continue

        keyword = _keywords_from_title(title)
        log.info(f"  Searching CJ for vid {vid} via keyword: '{keyword}'")
        pid = _cj_search_for_pid(keyword, vid)
        if not pid:
            log.warning(f"  Could not find matching pid — skipping")
            continue

        log.info(f"  Found pid: {pid}")
        images = fetch_images_by_pid(pid)
        if len(images) <= len(current_imgs):
            log.info(f"  No improvement ({len(images)} vs {len(current_imgs)}) — skipping")
            continue

        ok = put_ebay_images(sku, images, existing)
        if ok:
            log.info(f"  Updated: {len(current_imgs)} → {len(images)} images")
        else:
            log.warning("  eBay PUT failed")

        time.sleep(2)

    log.info("Done.")


if __name__ == "__main__":
    main()
