"""
Amazon UK as a sourcing channel, driven by a real (headless) browser via Playwright.

No Amazon buying API exists for ordinary buyers, so this module only READS:
  - search_amazon()      → candidates for a keyword (same dict shape as supplier.py)
  - check_amazon_listing() → current price / in-stock state for one ASIN

Ordering is manual: orders for supplier="amazon" send an alert with the ASIN
link + buyer address (see supplier._place_amazon_order).
"""

import logging
import random
import re
import threading
import time
from urllib.parse import quote_plus

log = logging.getLogger(__name__)

_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")

# Quality gates — a bad Amazon source becomes a bad eBay review.
MIN_RATING = 4.0
MIN_REVIEWS = 50
import os as _os
# With Prime, delivery is free; otherwise assume a typical paid delivery charge.
DEFAULT_PAID_DELIVERY_GBP = 0.0 if _os.getenv("AMAZON_FREE_DELIVERY", "0") == "1" else 3.99

_lock = threading.Lock()
_pw = None
_browser = None
_context = None
_blocked_until = 0.0  # backoff after a CAPTCHA / robot check


def _get_context():
    global _pw, _browser, _context
    if _context:
        return _context
    from playwright.sync_api import sync_playwright
    _pw = sync_playwright().start()
    _browser = _pw.chromium.launch(headless=True, args=["--no-sandbox", "--disable-dev-shm-usage"])
    _context = _browser.new_context(
        user_agent=_UA, locale="en-GB", timezone_id="Europe/London",
        viewport={"width": 1366, "height": 900},
    )
    # The server's datacentre IP is not in the UK; pin currency/language so prices come back in GBP.
    _context.add_cookies([
        {"name": "i18n-prefs", "value": "GBP", "domain": ".amazon.co.uk", "path": "/"},
        {"name": "lc-acbuk", "value": "en_GB", "domain": ".amazon.co.uk", "path": "/"},
    ])
    # Images/fonts/media aren't needed to read text; saves memory and bandwidth.
    _context.route(
        "**/*",
        lambda r: r.abort() if r.request.resource_type in ("font", "media") else r.continue_(),
    )
    return _context


def close():
    global _pw, _browser, _context
    try:
        if _context:
            _context.close()
        if _browser:
            _browser.close()
        if _pw:
            _pw.stop()
    except Exception:
        pass
    _pw = _browser = _context = None


def _is_blocked(page) -> bool:
    try:
        t = page.title().lower()
        if "robot check" in t or "sorry" in t:
            return True
        return page.locator("form[action*='validateCaptcha'], #captchacharacters").count() > 0
    except Exception:
        return False


def _goto(page, url: str) -> bool:
    global _blocked_until
    page.goto(url, wait_until="domcontentloaded", timeout=30000)
    try:  # UK cookie banner
        page.locator("#sp-cc-accept").click(timeout=1500)
    except Exception:
        pass
    if _is_blocked(page):
        _blocked_until = time.time() + 30 * 60
        log.warning("Amazon served a robot check — pausing Amazon browsing for 30 min")
        return False
    return True


def _avail_text(page) -> str:
    """Availability blurb; tolerant of pages with several/odd #availability nodes."""
    try:
        return page.locator("#availability").first.inner_text(timeout=2000).lower()
    except Exception:
        return ""


def _price(text: str | None) -> float | None:
    if not text:
        return None
    m = re.search(r"£\s*([\d,]+(?:\.\d{1,2})?)", text)
    return float(m.group(1).replace(",", "")) if m else None


def _hires(img_url: str) -> str:
    """Amazon thumbnails carry a size token (._AC_UY218_.) — strip it for the full image."""
    return re.sub(r"\._[A-Za-z0-9_,%-]+_\.", ".", img_url or "")


def search_amazon(keyword: str, max_price_gbp: float, limit: int = 4, strict: bool = True,
                  min_reviews: int | None = None) -> list[dict]:
    """
    Search amazon.co.uk and return up to `limit` candidates in supplier.py's shape.
    Skips sponsored cards, low-rated / low-review products and anything over max_price_gbp.
    """
    if time.time() < _blocked_until:
        return []
    with _lock:
        page = None
        try:
            page = _get_context().new_page()
            if not _goto(page, f"https://www.amazon.co.uk/s?k={quote_plus(keyword)}"):
                return []
            time.sleep(random.uniform(1.0, 2.0))
            cards = page.locator("div[data-component-type='s-search-result'][data-asin]").all()
            out = []
            for c in cards[:24]:
                asin = (c.get_attribute("data-asin") or "").strip()
                if not asin:
                    continue
                text = c.inner_text()
                if re.search(r"\bSponsored\b", text):
                    continue
                price = _price(c.locator(".a-price .a-offscreen").first.text_content()
                               if c.locator(".a-price .a-offscreen").count() else None)
                if not price or price > max_price_gbp:
                    continue
                rating, reviews = 0.0, 0
                rl = c.locator("[aria-label*='out of 5 stars']")
                if rl.count():
                    m = re.search(r"([\d.]+) out of 5", rl.first.get_attribute("aria-label") or "")
                    rating = float(m.group(1)) if m else 0.0
                rv = c.locator("a[href*='#customerReviews'] span, span.s-underline-text")
                if rv.count():
                    m = re.search(r"([\d.,]+)\s*([KkMm]?)", rv.first.inner_text())
                    if m:
                        n = float(m.group(1).replace(",", ""))
                        reviews = int(n * {"": 1, "k": 1000, "m": 1000000}[m.group(2).lower()])
                if rating < MIN_RATING or reviews < (MIN_REVIEWS if min_reviews is None else min_reviews):
                    continue
                # Some card layouts put only the brand in the first <h2>; take the longest of the h2 texts
                # and the product image's alt text, which carries the full title.
                texts = []
                for h2 in c.locator("h2").all():
                    try:
                        texts.append(h2.inner_text().strip())
                    except Exception:
                        pass
                alt = c.locator("img.s-image")
                if alt.count():
                    texts.append((alt.first.get_attribute("alt") or "").strip())
                title = max(texts, key=len) if texts else keyword
                # Relevance: every meaningful keyword word (matched on its first 5 letters) must
                # appear in the title, otherwise "electric planer" matches a manual hand plane.
                words = [w[:5] for w in re.findall(r"[a-z0-9]+", keyword.lower())
                         if len(w) >= 4 and w not in ("with", "for", "and", "the")]
                if strict and any(w not in title.lower() for w in words):
                    continue
                img_el = c.locator("img.s-image")
                img = _hires(img_el.first.get_attribute("src")) if img_el.count() else ""
                prime = c.locator("i.a-icon-prime").count() > 0
                free_delivery = bool(re.search(r"FREE\s+(?:UK\s+)?[Dd]elivery", text)) or prime
                out.append({
                    "supplier": "amazon",
                    "product_id": asin,
                    "title": title,
                    "cost_gbp": price,
                    "shipping_gbp": 0.0 if free_delivery else DEFAULT_PAID_DELIVERY_GBP,
                    "delivery_days": 3 if prime else 5,
                    "images": [img] if img else [],
                    "description": "",
                    "uk_warehouse": True,
                    "amazon_rating": rating,
                    "amazon_reviews": reviews,
                })
                if len(out) >= limit:
                    break
            log.info(f"Amazon: {len(out)} candidate(s) for '{keyword}'")
            return out
        except Exception as e:
            log.warning(f"Amazon search failed for '{keyword}': {e}")
            close()  # drop a possibly wedged browser; next call restarts it
            return []
        finally:
            try:
                if page:
                    page.close()
            except Exception:
                pass


def check_amazon_listing(asin: str) -> dict:
    """
    Open the product page. Returns {"found": bool, "in_stock": bool|None, "price": float|None,
    "blocked": bool}. in_stock is None when the page could not be read.
    """
    if time.time() < _blocked_until:
        return {"found": False, "in_stock": None, "price": None, "blocked": True}
    with _lock:
        page = None
        try:
            page = _get_context().new_page()
            if not _goto(page, f"https://www.amazon.co.uk/dp/{asin}"):
                return {"found": False, "in_stock": None, "price": None, "blocked": True}
            time.sleep(random.uniform(1.0, 2.0))
            if page.locator("#productTitle").count() == 0:
                return {"found": False, "in_stock": False, "price": None, "blocked": False}
            price = None
            for sel in ("#corePrice_feature_div .a-offscreen", "#corePriceDisplay_desktop_feature_div .a-offscreen",
                        ".a-price .a-offscreen"):
                loc = page.locator(sel)
                if loc.count():
                    price = _price(loc.first.text_content())
                    if price:
                        break
            avail = _avail_text(page)
            can_buy = page.locator("#add-to-cart-button, #buy-now-button").count() > 0
            in_stock = bool(can_buy and "unavailable" not in avail and price)
            return {"found": True, "in_stock": in_stock, "price": price, "blocked": False}
        except Exception as e:
            log.warning(f"Amazon check failed for {asin}: {e}")
            close()
            return {"found": False, "in_stock": None, "price": None, "blocked": False}
        finally:
            try:
                if page:
                    page.close()
            except Exception:
                pass


def get_product(asin: str) -> dict | None:
    """
    Full product-page read for one ASIN (used when a product is supplied by hand).
    Returns a supplier-style dict (cost_gbp is the VAT-inclusive shelf price), or None.
    """
    if time.time() < _blocked_until:
        return None
    with _lock:
        page = None
        try:
            page = _get_context().new_page()
            if not _goto(page, f"https://www.amazon.co.uk/dp/{asin}"):
                return None
            time.sleep(random.uniform(1.0, 2.0))
            if page.locator("#productTitle").count() == 0:
                return None
            title = page.locator("span#productTitle").first.inner_text().strip()
            price = None
            for sel in ("#corePrice_feature_div .a-offscreen", "#corePriceDisplay_desktop_feature_div .a-offscreen",
                        ".a-price .a-offscreen"):
                loc = page.locator(sel)
                if loc.count():
                    price = _price(loc.first.text_content())
                    if price:
                        break
            images = []
            if page.locator("#landingImage").count():
                import json as _json
                try:
                    dyn = _json.loads(page.locator("#landingImage").first.get_attribute("data-a-dynamic-image") or "{}")
                    images = sorted(dyn, key=lambda u: -max(dyn[u]))[:1]
                except Exception:
                    pass
            for t in page.locator("#altImages img").all()[:8]:
                src = _hires(t.get_attribute("src") or "")
                if src and "sprite" not in src and "play-icon" not in src and src not in images:
                    images.append(src)
            # The page's own gallery data carries the full-size ("hiRes") image of every photo.
            try:
                hi = list(dict.fromkeys(re.findall(r'"hiRes":"(https:[^"]+?\.jpg)"', page.content())))
                if len(hi) >= 2:
                    images = hi[:8]
            except Exception:
                pass
            bullets = [b.inner_text().strip() for b in page.locator("#feature-bullets li").all()[:8]]
            aspects = {}
            try:
                by = page.locator("#bylineInfo").first.inner_text(timeout=1500).strip()
                brand = re.sub(r"^(Visit the |Brand:\s*)", "", by)
                brand = re.sub(r"\s+Store$", "", brand).strip()
                if brand and len(brand) < 40:
                    aspects["Brand"] = [brand]
            except Exception:
                pass
            try:
                row = page.locator("tr:has(th:has-text('Item model number')) td, "
                                   "li:has(span:has-text('Item model number')) span").last
                model = row.inner_text(timeout=1500).strip()
                if model and len(model) < 40:
                    aspects["Model"] = [model]
            except Exception:
                pass
            avail = _avail_text(page)
            in_stock = page.locator("#add-to-cart-button, #buy-now-button").count() > 0 and "unavailable" not in avail
            prime = page.locator("i.a-icon-prime").count() > 0
            return {
                "supplier": "amazon", "product_id": asin, "title": title,
                "cost_gbp": price or 0.0, "shipping_gbp": 0.0 if prime else DEFAULT_PAID_DELIVERY_GBP,
                "delivery_days": 3 if prime else 5, "images": images[:8],
                "description": " ".join(bullets), "uk_warehouse": True, "in_stock": in_stock,
                "aspects": aspects,
            }
        except Exception as e:
            log.warning(f"Amazon product read failed for {asin}: {e}")
            close()
            return None
        finally:
            try:
                if page:
                    page.close()
            except Exception:
                pass


if __name__ == "__main__":
    import json
    import sys
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    kw = " ".join(sys.argv[1:]) or "laser measure digital"
    res = search_amazon(kw, 80)
    print(json.dumps(res, indent=1)[:3000])
    if res:
        print(check_amazon_listing(res[0]["product_id"]))
    close()
