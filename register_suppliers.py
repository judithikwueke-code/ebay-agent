"""
Automated supplier registration via Playwright.
Attempts to register with BigBuy, Wholesale2B, Spocket.
Sends Telegram message with results + any steps requiring manual action.
"""
import time, traceback
from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

TOKEN = "8583121219:AAFbpza_GbFcfzjp8_mDZAGgWbZ5sAS9Z14"
CHAT_ID = "7681216735"
EMAIL = "07487863927n@gmail.com"
BUSINESS_NAME = "Yagz Ltd"
FIRST = "Judith"
LAST = "Ikwueke"

import requests as _req

def tg(msg):
    _req.post(f"https://api.telegram.org/bot{TOKEN}/sendMessage",
              json={"chat_id": CHAT_ID, "text": msg}, timeout=10)

def try_bigbuy(p):
    """BigBuy EU — large catalogue, UK fulfilment available."""
    result = {"name": "BigBuy", "status": "unknown", "notes": ""}
    try:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto("https://www.bigbuy.eu/en/register", timeout=30000)
        page.wait_for_load_state("networkidle", timeout=15000)

        # Fill registration form
        for sel, val in [
            ("input[name='email'], input[type='email']", EMAIL),
            ("input[name='firstName'], input[name='first_name'], #firstName", FIRST),
            ("input[name='lastName'], input[name='last_name'], #lastName", LAST),
            ("input[name='company'], input[name='companyName'], #company", BUSINESS_NAME),
        ]:
            for s in sel.split(", "):
                try:
                    el = page.locator(s).first
                    if el.is_visible(timeout=2000):
                        el.fill(val)
                        break
                except Exception:
                    pass

        # Check for CAPTCHA
        if page.locator("iframe[src*='recaptcha'], .g-recaptcha, iframe[title*='reCAPTCHA']").count() > 0:
            result["status"] = "needs_manual"
            result["notes"] = "CAPTCHA encountered — go to https://www.bigbuy.eu/en/register and complete signup manually"
        else:
            # Try submit
            for btn in ["button[type='submit']", "input[type='submit']", "button:has-text('Register')", "button:has-text('Sign up')"]:
                try:
                    page.locator(btn).first.click(timeout=5000)
                    page.wait_for_timeout(3000)
                    break
                except Exception:
                    pass

            if "verify" in page.url.lower() or "confirm" in page.url.lower() or "thank" in page.url.lower():
                result["status"] = "registered_needs_email_verify"
                result["notes"] = f"Check {EMAIL} for BigBuy verification email"
            elif "already" in page.content().lower() or "exists" in page.content().lower():
                result["status"] = "already_registered"
                result["notes"] = f"Account may already exist for {EMAIL}"
            else:
                result["status"] = "needs_manual"
                result["notes"] = "Could not confirm registration — go to https://www.bigbuy.eu/en/register"

        browser.close()
    except Exception as e:
        result["status"] = "error"
        result["notes"] = f"Error: {str(e)[:100]} — try manually at https://www.bigbuy.eu/en/register"
    return result


def try_spocket(p):
    """Spocket — UK/EU focused supplier platform, integrates with eBay."""
    result = {"name": "Spocket", "status": "unknown", "notes": ""}
    try:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto("https://app.spocket.co/register", timeout=30000)
        page.wait_for_load_state("networkidle", timeout=15000)

        for sel, val in [
            ("input[name='email'], input[type='email'], input[placeholder*='email' i]", EMAIL),
            ("input[name='full_name'], input[name='name'], input[placeholder*='name' i]", f"{FIRST} {LAST}"),
        ]:
            for s in sel.split(", "):
                try:
                    el = page.locator(s).first
                    if el.is_visible(timeout=2000):
                        el.fill(val)
                        break
                except Exception:
                    pass

        if page.locator("iframe[src*='recaptcha']").count() > 0:
            result["status"] = "needs_manual"
            result["notes"] = "CAPTCHA — complete at https://app.spocket.co/register"
        else:
            try:
                page.locator("button[type='submit'], button:has-text('Get Started'), button:has-text('Sign up')").first.click(timeout=5000)
                page.wait_for_timeout(3000)
            except Exception:
                pass

            url = page.url
            if "dashboard" in url or "products" in url:
                result["status"] = "registered"
                result["notes"] = "Registered and logged in!"
            else:
                result["status"] = "needs_manual"
                result["notes"] = "Go to https://app.spocket.co/register — pre-filled with your email"

        browser.close()
    except Exception as e:
        result["status"] = "error"
        result["notes"] = f"Error: {str(e)[:100]} — try manually at https://app.spocket.co/register"
    return result


def try_wholesale2b(p):
    """Wholesale2B — native eBay dropship integration."""
    result = {"name": "Wholesale2B", "status": "unknown", "notes": ""}
    try:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto("https://www.wholesale2b.com/register.php", timeout=30000)
        page.wait_for_load_state("networkidle", timeout=15000)

        for sel, val in [
            ("input[name='email'], input[type='email']", EMAIL),
            ("input[name='fname'], input[name='first_name']", FIRST),
            ("input[name='lname'], input[name='last_name']", LAST),
        ]:
            for s in sel.split(", "):
                try:
                    el = page.locator(s).first
                    if el.is_visible(timeout=2000):
                        el.fill(val)
                        break
                except Exception:
                    pass

        if page.locator("iframe[src*='recaptcha'], .g-recaptcha").count() > 0:
            result["status"] = "needs_manual"
            result["notes"] = "CAPTCHA — complete at https://www.wholesale2b.com/register.php"
        else:
            try:
                page.locator("button[type='submit'], input[type='submit']").first.click(timeout=5000)
                page.wait_for_timeout(3000)
                result["status"] = "needs_manual"
                result["notes"] = f"Check {EMAIL} for confirmation or go to https://www.wholesale2b.com/register.php"
            except Exception:
                result["status"] = "needs_manual"
                result["notes"] = "Go to https://www.wholesale2b.com/register.php"

        browser.close()
    except Exception as e:
        result["status"] = "error"
        result["notes"] = str(e)[:100]
    return result


def main():
    tg("Starting automated supplier registration...\nAttempting: BigBuy, Spocket, Wholesale2B")

    results = []
    with sync_playwright() as p:
        for fn in [try_bigbuy, try_spocket, try_wholesale2b]:
            try:
                r = fn(p)
                results.append(r)
                print(f"{r['name']}: {r['status']} — {r['notes']}")
            except Exception as e:
                results.append({"name": fn.__name__, "status": "crash", "notes": str(e)[:80]})
            time.sleep(2)

    lines = ["SUPPLIER REGISTRATION RESULTS\n"]
    manual = []
    for r in results:
        icon = "OK" if r["status"] in ("registered", "registered_needs_email_verify", "already_registered") else "ACTION NEEDED"
        lines.append(f"[{icon}] {r['name']}: {r['status']}\n  {r['notes']}")
        if "manual" in r["status"] or "verify" in r["status"]:
            manual.append(f"- {r['name']}: {r['notes']}")

    if manual:
        lines.append("\nYOUR ACTION REQUIRED:")
        lines.extend(manual)

    tg("\n".join(lines))
    print("\n".join(lines))

if __name__ == "__main__":
    main()
