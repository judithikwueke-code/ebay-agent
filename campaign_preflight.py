"""
Preflight for campaign runs: exits non-zero (and sends a Telegram message) if a hard dependency is down,
so a run never burns eBay API quota or half-fails.  Checks: eBay Browse quota (429) and Anthropic credits.
"""
import os
import sys

os.environ.setdefault("SEASONAL_ONLY", "0")
from dotenv import load_dotenv

load_dotenv()
problems = []

try:
    import requests
    from config import get_app_token
    r = requests.get("https://api.ebay.com/buy/browse/v1/item_summary/search",
                     headers={"Authorization": "Bearer " + get_app_token(), "X-EBAY-C-MARKETPLACE-ID": "EBAY_GB"},
                     params={"q": "torch", "limit": 1}, timeout=15)
    if r.status_code == 429:
        problems.append("eBay Browse API daily quota exhausted (resets 08:00 UTC)")
    elif not r.ok:
        problems.append(f"eBay Browse API error {r.status_code}")
except Exception as e:
    problems.append(f"eBay Browse API check failed: {e}")

try:
    import anthropic
    from config import ANTHROPIC_API_KEY
    anthropic.Anthropic(api_key=ANTHROPIC_API_KEY).messages.create(
        model="claude-haiku-4-5", max_tokens=5, messages=[{"role": "user", "content": "hi"}])
except Exception as e:
    msg = str(e)
    problems.append("Anthropic API credits too low - top up at console.anthropic.com" if "credit balance" in msg
                    else f"Anthropic API check failed: {msg[:120]}")

if problems:
    print("PREFLIGHT FAILED: " + "; ".join(problems))
    try:
        from telegram_bot import _send
        _send("BE READY CAMPAIGN RUN NOT STARTED:" + chr(10) + chr(10).join("- " + p for p in problems))
    except Exception:
        pass
    sys.exit(1)
print("PREFLIGHT OK")
