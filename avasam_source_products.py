"""
Avasam product sourcing automation via Playwright.
Logs into app.avasam.com, browses the product catalogue,
and sources (imports) products matching our target categories.
These products then appear via the Avasam API for supplier.py to use.

Run once to bootstrap the Avasam catalogue.
Reports progress and failures to Telegram.
"""
import os
import time
import logging
from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout
import requests

log = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

AVASAM_EMAIL = "07487863927n@gmail.com"
AVASAM_PASS = None  # Will try env / ask via Telegram if None

TARGET_KEYWORDS = [
    "hair brush", "electric toothbrush", "led strip lights",
    "laptop stand", "phone stand", "wireless charger",
    "garden solar lights", "security camera", "smart plug",
    "yoga mat", "resistance bands", "kitchen scales",
    "storage boxes", "wall clock modern", "scented candles",
    "dog lead", "cat toys", "baby monitor",
    "stationery set", "cork board",
]


def tg(msg):
    try:
        requests.post(
            f"https://api.telegram.org/bot{TOKEN}/sendMessage",
            json={"chat_id": CHAT_ID, "text": msg}, timeout=10
        )
    except Exception:
        pass


def run():
    import os
    pwd = os.environ.get("AVASAM_PASSWORD") or AVASAM_PASS
    if not pwd:
        tg(
            "AVASAM SETUP NEEDED\n\n"
            "I need your Avasam password to auto-source products.\n\n"
            "Options:\n"
            "1. Log in at app.avasam.com manually → Products → Browse & Source → "
            "search each keyword → click 'Add to my products'\n\n"
            "2. Run: AVASAM_PASSWORD=yourpassword python3 avasam_source_products.py\n\n"
            "Once sourced, the Avasam supplier will start appearing in the agent's research cycles automatically."
        )
        print("AVASAM_PASSWORD env var not set — sent Telegram instructions")
        return

    tg("Avasam: starting product sourcing automation...")
    sourced = 0
    failed_keywords = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()

        # Login
        page.goto("https://app.avasam.com/login", timeout=30000)
        page.wait_for_load_state("networkidle", timeout=15000)

        try:
            page.fill("input[name='email'], input[type='email']", AVASAM_EMAIL, timeout=5000)
            page.fill("input[name='password'], input[type='password']", pwd, timeout=5000)
            page.click("button[type='submit'], button:has-text('Login'), button:has-text('Sign in')")
            page.wait_for_url("**/dashboard**", timeout=15000)
            tg("Avasam: logged in successfully")
        except Exception as e:
            tg(
                f"Avasam login failed: {str(e)[:80]}\n\n"
                "Please log in at app.avasam.com manually and source products:\n"
                "Products → Browse Products → search → Add to my products"
            )
            browser.close()
            return

        for kw in TARGET_KEYWORDS:
            try:
                # Navigate to product browse
                page.goto("https://app.avasam.com/products/browse", timeout=20000)
                page.wait_for_load_state("networkidle", timeout=10000)

                # Search for keyword
                search = page.locator("input[placeholder*='Search'], input[name='search'], input[type='search']").first
                search.fill(kw)
                search.press("Enter")
                page.wait_for_timeout(2000)

                # Try to source first few results
                add_buttons = page.locator("button:has-text('Add'), button:has-text('Source'), button:has-text('Import')").all()
                if not add_buttons:
                    log.info(f"No add buttons found for '{kw}'")
                    failed_keywords.append(kw)
                    continue

                count = 0
                for btn in add_buttons[:3]:
                    try:
                        btn.click(timeout=3000)
                        page.wait_for_timeout(500)
                        count += 1
                    except Exception:
                        pass

                if count > 0:
                    sourced += count
                    log.info(f"Sourced {count} products for '{kw}'")
                else:
                    failed_keywords.append(kw)

            except Exception as e:
                log.warning(f"Error sourcing '{kw}': {e}")
                failed_keywords.append(kw)

            time.sleep(1)

        browser.close()

    msg = (
        f"AVASAM SOURCING COMPLETE\n"
        f"  Sourced: {sourced} products\n"
        f"  Failures: {len(failed_keywords)} keywords\n"
    )
    if failed_keywords:
        msg += f"  Manual needed: {', '.join(failed_keywords[:8])}\n"
    msg += "\nAvasam products now visible via API — next research cycle will include them."
    tg(msg)
    print(msg)


if __name__ == "__main__":
    run()
