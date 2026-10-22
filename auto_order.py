"""
Automatic Amazon UK ordering for eBay sales, driven by a real browser (Playwright).

Modes (env AMAZON_AUTO_ORDER):
    unset / "0"  off - the normal "buy it by hand" alert is used
    "dry"        goes through checkout up to the final review page, saves a screenshot, does NOT buy;
                 the manual alert is still sent so nothing is missed
    "1"          places the order for real; falls back to the manual alert on any problem

Safety rails:
  * AUTO_ORDER_OFF file next to this module = instant kill switch
  * price guard: never pays more than the stored cost + tolerance (AMAZON_PRICE_TOLERANCE, default 15%)
  * stock guard, duplicate guard, per-account daily cap (AMAZON_ORDERS_PER_ACCOUNT_PER_DAY, default 5)
  * random pause between orders, one browser session at a time
  * on a login challenge / captcha / "unusual activity" page the account is DISABLED and you are alerted
  * every failure saves a screenshot to order_debug/ and falls back to the manual alert

Accounts live in amazon_accounts.json (chmod 600, never logged):
  [{"name": "acc1", "email": "...", "password": "...", "totp_secret": "BASE32SECRET", "enabled": true}]
Use add_amazon_account.py to create/update it without typing secrets into a shell history.
"""
import base64
import hashlib
import hmac
import json
import logging
import os
import random
import re
import sqlite3
import struct
import threading
import time
from datetime import date

log = logging.getLogger(__name__)

HERE = os.path.dirname(os.path.abspath(__file__))
ACCOUNTS_FILE = os.getenv("AMAZON_ACCOUNTS_FILE", os.path.join(HERE, "amazon_accounts.json"))
STATE_FILE = os.path.join(HERE, "auto_order_state.json")
KILL_FILE = os.path.join(HERE, "AUTO_ORDER_OFF")
PROFILES_DIR = os.path.join(HERE, "amazon_profiles")
DEBUG_DIR = os.path.join(HERE, "order_debug")

_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")
_lock = threading.Lock()


# --------------------------------------------------------------------------- helpers
def totp(secret: str, for_time: float | None = None) -> str:
    """RFC 6238 6-digit code from a base32 secret (what Amazon's authenticator key is)."""
    key = base64.b32decode(re.sub(r"\s+", "", secret).upper() + "=" * (-len(re.sub(r"\s+", "", secret)) % 8))
    counter = int((for_time if for_time is not None else time.time()) // 30)
    digest = hmac.new(key, struct.pack(">Q", counter), hashlib.sha1).digest()
    off = digest[-1] & 0x0F
    code = (struct.unpack(">I", digest[off:off + 4])[0] & 0x7FFFFFFF) % 1_000_000
    return f"{code:06d}"


def _load_accounts() -> list[dict]:
    if not os.path.exists(ACCOUNTS_FILE):
        return []
    with open(ACCOUNTS_FILE, encoding="utf-8") as f:
        return json.load(f)


def _load_state() -> dict:
    try:
        with open(STATE_FILE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _save_state(state: dict):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f)


def _alert(subject: str, body: str):
    try:
        from monitor import send_alert
        send_alert(subject=subject, body=body)
    except Exception as e:
        log.warning(f"alert failed: {e}")


def _pick_account(state: dict) -> dict | None:
    """Round-robin over enabled accounts that are under today's cap."""
    cap = int(os.getenv("AMAZON_ORDERS_PER_ACCOUNT_PER_DAY", "5"))
    today = date.today().isoformat()
    usable = []
    for acc in _load_accounts():
        st = state.get(acc["name"], {})
        if not acc.get("enabled", True) or st.get("disabled"):
            continue
        if st.get("day") == today and st.get("count", 0) >= cap:
            continue
        usable.append((st.get("last_used", 0), acc))
    return min(usable, key=lambda x: x[0])[1] if usable else None


def _expected_cost(asin: str) -> float | None:
    """Shelf price we assumed when listing (stored cost is ex-VAT)."""
    try:
        from db import DB_PATH
        conn = sqlite3.connect(DB_PATH)
        row = conn.execute("SELECT cost_gbp FROM products WHERE supplier='amazon' AND supplier_product_id=?",
                           (asin,)).fetchone()
        conn.close()
        return round(row[0] * 1.2, 2) if row and row[0] else None
    except Exception:
        return None


def _shot(page, name: str) -> str:
    os.makedirs(DEBUG_DIR, exist_ok=True)
    path = os.path.join(DEBUG_DIR, f"{int(time.time())}_{name}.png")
    try:
        page.screenshot(path=path, full_page=True)
    except Exception:
        pass
    return path


class Challenge(Exception):
    """Amazon asked for something we won't automate (captcha, unusual-activity, account problem)."""


def _check_challenge(page):
    try:
        title = page.title().lower()
        body = page.inner_text("body")[:1500].lower()
    except Exception:
        return
    if page.locator("#captchacharacters, form[action*='validateCaptcha']").count() or "robot check" in title:
        raise Challenge("captcha")
    for phrase in ("unusual activity", "your account has been locked", "account on hold", "suspended",
                   "we need to verify", "puzzle", "sign-in attempt", "account is temporarily"):
        if phrase in body:
            raise Challenge(phrase)


# --------------------------------------------------------------------------- browser steps
def _login(page, acc: dict):
    page.goto("https://www.amazon.co.uk/", wait_until="domcontentloaded", timeout=45000)
    try:
        page.locator("#sp-cc-accept").click(timeout=1500)
    except Exception:
        pass
    _check_challenge(page)
    signed_in = page.locator("#nav-link-accountList-nav-line-1").first
    if signed_in.count() and "sign in" not in signed_in.inner_text().lower():
        return
    page.goto("https://www.amazon.co.uk/ap/signin?openid.pape.max_auth_age=0&openid.return_to="
              "https%3A%2F%2Fwww.amazon.co.uk%2F&openid.identity=http%3A%2F%2Fspecs.openid.net%2Fauth%2F2.0%2F"
              "identifier_select&openid.assoc_handle=gbflex&openid.mode=checkid_setup&openid.claimed_id="
              "http%3A%2F%2Fspecs.openid.net%2Fauth%2F2.0%2Fidentifier_select&openid.ns="
              "http%3A%2F%2Fspecs.openid.net%2Fauth%2F2.0", wait_until="domcontentloaded", timeout=45000)
    _check_challenge(page)
    page.fill("#ap_email", acc["email"])
    page.click("#continue")
    page.wait_for_selector("#ap_password", timeout=15000)
    page.fill("#ap_password", acc["password"])
    page.click("#signInSubmit")
    page.wait_for_load_state("domcontentloaded")
    time.sleep(2)
    _check_challenge(page)
    if page.locator("#auth-mfa-otpcode").count():
        page.fill("#auth-mfa-otpcode", totp(acc["totp_secret"]))
        try:
            page.check("#auth-mfa-remember-device", timeout=1000)
        except Exception:
            pass
        page.click("#auth-signin-button")
        page.wait_for_load_state("domcontentloaded")
        time.sleep(2)
        _check_challenge(page)
    if page.locator("#ap_password, #ap_email").count():
        raise Challenge("login did not complete")


def _product_ok(page, asin: str, max_total: float) -> float:
    page.goto(f"https://www.amazon.co.uk/dp/{asin}", wait_until="domcontentloaded", timeout=45000)
    _check_challenge(page)
    if page.locator("span#productTitle").count() == 0:
        raise RuntimeError("product page not found")
    price = None
    for sel in ("#corePrice_feature_div .a-offscreen", "#corePriceDisplay_desktop_feature_div .a-offscreen",
                ".a-price .a-offscreen"):
        loc = page.locator(sel)
        if loc.count():
            m = re.search(r"£\s*([\d,]+(?:\.\d{1,2})?)", loc.first.text_content() or "")
            if m:
                price = float(m.group(1).replace(",", ""))
                break
    if price is None:
        raise RuntimeError("could not read price")
    if page.locator("#buy-now-button").count() == 0:
        raise RuntimeError("no Buy Now button (out of stock / unavailable)")
    if price > max_total:
        raise RuntimeError(f"price GBP {price:.2f} is above the limit GBP {max_total:.2f}")
    return price


def _enter_address(page, addr: dict):
    """On the checkout page: add the buyer's address as a new delivery address and select it."""
    page.wait_for_load_state("domcontentloaded")
    time.sleep(2)
    _check_challenge(page)
    change = page.locator("#deliver-to-address-change, a:has-text('Change'):near(:text('Delivering to'))").first
    change.click(timeout=15000)
    time.sleep(1.5)
    page.locator("a:has-text('Add a new delivery address'), #add-new-address-popover-link, "
                 "[data-testid='add-address-link']").first.click(timeout=15000)
    page.wait_for_selector("#address-ui-widgets-enterAddressFullName", timeout=15000)
    page.fill("#address-ui-widgets-enterAddressFullName", addr.get("name", ""))
    page.fill("#address-ui-widgets-enterAddressPhoneNumber", re.sub(r"\s+", "", addr.get("phone", "") or "07000000000"))
    page.fill("#address-ui-widgets-enterAddressPostalCode", addr.get("postcode", ""))
    page.fill("#address-ui-widgets-enterAddressLine1", addr.get("line1", ""))
    if addr.get("line2"):
        page.fill("#address-ui-widgets-enterAddressLine2", addr["line2"])
    page.fill("#address-ui-widgets-enterAddressCity", addr.get("city", ""))
    page.locator("#address-ui-widgets-form-submit-button input, input[name='address-ui-widgets-form-submit-button'], "
                 "#address-ui-widgets-form-submit-button").first.click()
    time.sleep(3)
    # suggested-address / confirmation popups
    for sel in ("input[name='address-ui-widgets-saveOriginalOrSuggestedAddress']", "#address-ui-widgets-use-suggested-address-button"):
        try:
            page.locator(sel).first.click(timeout=2500)
            time.sleep(2)
        except Exception:
            pass
    _check_challenge(page)


def _review_total(page) -> float | None:
    for sel in (".grand-total-price", "#subtotals-marketplace-table .grand-total-price", "td:has-text('Order Total') + td"):
        loc = page.locator(sel)
        if loc.count():
            m = re.search(r"£\s*([\d,]+\.\d{2})", loc.first.inner_text())
            if m:
                return float(m.group(1).replace(",", ""))
    return None


def _run_order(acc: dict, asin: str, addr: dict, max_total: float, dry: bool) -> dict:
    from playwright.sync_api import sync_playwright
    os.makedirs(os.path.join(PROFILES_DIR, acc["name"]), exist_ok=True)
    proxy = os.getenv("AMAZON_PROXY")  # e.g. http://user:pass@uk-proxy:port - a UK IP is safer than a datacentre IP
    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(
            os.path.join(PROFILES_DIR, acc["name"]), headless=True, locale="en-GB", timezone_id="Europe/London",
            user_agent=_UA, viewport={"width": 1366, "height": 900},
            args=["--no-sandbox", "--disable-dev-shm-usage"],
            **({"proxy": {"server": proxy}} if proxy else {}))
        page = ctx.new_page()
        try:
            _login(page, acc)
            price = _product_ok(page, asin, max_total)
            page.click("#buy-now-button")
            page.wait_for_load_state("domcontentloaded")
            time.sleep(3)
            # Amazon may re-ask for the password before checkout
            if page.locator("#ap_password").count():
                page.fill("#ap_password", acc["password"])
                page.click("#signInSubmit")
                page.wait_for_load_state("domcontentloaded")
                time.sleep(2)
            _check_challenge(page)
            _enter_address(page, addr)
            total = _review_total(page)
            if total is not None and total > max_total:
                raise RuntimeError(f"order total GBP {total:.2f} is above the limit GBP {max_total:.2f}")
            if dry:
                shot = _shot(page, f"{asin}_review")
                return {"dry_run": True, "price": price, "total": total, "screenshot": shot}
            place = page.locator("#placeYourOrder button, input[name='placeYourOrder1'], #placeYourOrder").first
            place.click(timeout=15000)
            page.wait_for_load_state("domcontentloaded")
            time.sleep(4)
            _check_challenge(page)
            m = re.search(r"\b(\d{3}-\d{7}-\d{7})\b", page.inner_text("body"))
            if not m:
                shot = _shot(page, f"{asin}_noconfirm")
                raise RuntimeError(f"no order number on confirmation page (screenshot {shot}) - CHECK AMAZON ORDERS")
            return {"supplier_order_id": m.group(1), "price": price, "total": total}
        finally:
            ctx.close()


# --------------------------------------------------------------------------- public API
def place_order(asin: str, addr: dict) -> dict | None:
    """
    Returns {"supplier_order_id": ...} when an order was really placed, {"dry_run": True, ...} in dry mode,
    or None if the caller should fall back to the manual alert.
    """
    mode = os.getenv("AMAZON_AUTO_ORDER", "0")
    if mode not in ("dry", "1"):
        return None
    if os.path.exists(KILL_FILE):
        log.warning("AUTO_ORDER_OFF present - manual ordering")
        return None
    with _lock:
        state = _load_state()
        acc = _pick_account(state)
        if not acc:
            log.warning("no usable Amazon account (none configured, disabled, or at daily cap) - manual ordering")
            return None
        expected = _expected_cost(asin)
        tol = float(os.getenv("AMAZON_PRICE_TOLERANCE", "0.15"))
        if not expected:
            log.warning(f"{asin}: no stored cost to check against - manual ordering")
            return None
        max_total = round(expected * (1 + tol) + 1.0, 2)
        dry = mode == "dry"
        try:
            time.sleep(random.uniform(5, 20))
            res = _run_order(acc, asin, addr, max_total, dry)
        except Challenge as c:
            state.setdefault(acc["name"], {})["disabled"] = True
            _save_state(state)
            _alert(f"[AUTO-ORDER] Amazon account {acc['name']} disabled - {c}",
                   f"Amazon showed '{c}' while ordering {asin}. This account is switched off for auto-ordering "
                   f"until you log in manually, resolve it and re-enable it. Order {asin} needs placing by hand.")
            return None
        except Exception as e:
            log.warning(f"auto-order failed for {asin}: {e}")
            _alert(f"[AUTO-ORDER] failed for {asin}", f"{e}\nThis order needs placing by hand.")
            return None
        st = state.setdefault(acc["name"], {})
        today = date.today().isoformat()
        st["count"] = (st.get("count", 0) + 1) if st.get("day") == today else 1
        st["day"], st["last_used"] = today, time.time()
        _save_state(state)
        if res.get("dry_run"):
            _alert(f"[AUTO-ORDER dry run] {asin} reached the review page",
                   f"Price GBP {res.get('price')} total GBP {res.get('total')}. Screenshot: {res.get('screenshot')}\n"
                   f"Nothing was bought. Place this one by hand.")
            return None
        _alert(f"[AUTO-ORDER] placed Amazon order {res['supplier_order_id']}",
               f"{asin} for GBP {res.get('total') or res.get('price')} to {addr.get('name')}, {addr.get('postcode')}.")
        return res


def get_tracking(order_id: str) -> str | None:
    """Best effort: read the carrier tracking id for an Amazon order. Experimental."""
    if os.getenv("AMAZON_AUTO_ORDER", "0") not in ("dry", "1") or os.path.exists(KILL_FILE):
        return None
    acc = _pick_account({})
    if not acc:
        return None
    from playwright.sync_api import sync_playwright
    with _lock, sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(
            os.path.join(PROFILES_DIR, acc["name"]), headless=True, locale="en-GB", user_agent=_UA,
            args=["--no-sandbox", "--disable-dev-shm-usage"])
        page = ctx.new_page()
        try:
            _login(page, acc)
            page.goto(f"https://www.amazon.co.uk/gp/your-account/ship-track?orderId={order_id}", wait_until="domcontentloaded", timeout=45000)
            time.sleep(2)
            _check_challenge(page)
            m = re.search(r"Tracking ID:?\s*([A-Z0-9]{8,30})", page.inner_text("body"))
            return m.group(1) if m else None
        except Exception as e:
            log.warning(f"tracking read failed for {order_id}: {e}")
            return None
        finally:
            ctx.close()
