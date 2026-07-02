"""
One-time script to get eBay OAuth access_token + refresh_token.

Run:  python3 get_tokens.py
Then follow the instructions printed on screen.
"""

import base64
import os
import urllib.parse
import requests
from dotenv import load_dotenv

load_dotenv()

APP_ID    = os.getenv("EBAY_APP_ID")
CERT_ID   = os.getenv("EBAY_CERT_ID")

# eBay requires a RuName (redirect URL name) registered in your developer account.
# Go to: developer.ebay.com → "Get a User Token" → your RuName is shown there.
# It looks like: YagzLtd-YagzAgen-PRD-xxxxx-xxxxx
RU_NAME = input("Paste your RuName (from developer.ebay.com → User Tokens page): ").strip()

SCOPES = " ".join([
    "https://api.ebay.com/oauth/api_scope",
    "https://api.ebay.com/oauth/api_scope/sell.inventory",
    "https://api.ebay.com/oauth/api_scope/sell.fulfillment",
    "https://api.ebay.com/oauth/api_scope/sell.account",
    "https://api.ebay.com/oauth/api_scope/sell.analytics.readonly",
])

# Step 1 — build the authorisation URL
auth_url = (
    "https://auth.ebay.com/oauth2/authorize"
    f"?client_id={APP_ID}"
    f"&redirect_uri={urllib.parse.quote(RU_NAME)}"
    f"&response_type=code"
    f"&scope={urllib.parse.quote(SCOPES)}"
)

print("\n" + "="*60)
print("STEP 1: Open this URL in your browser and log in with your")
print("        eBay SELLER account:")
print()
print(auth_url)
print()
print("After authorising, eBay redirects you to a URL like:")
print("  https://your-redirect-url?code=v%5E1.1%23i%5E1...")
print("Copy the full redirect URL (or just the 'code=' value).")
print("="*60 + "\n")

raw = input("Paste the redirect URL (or just the code value): ").strip()

# Extract code from URL if the user pasted the full redirect URL
if "code=" in raw:
    code = urllib.parse.parse_qs(urllib.parse.urlparse(raw).query).get("code", [None])[0]
    if code:
        code = urllib.parse.unquote(code)
else:
    code = urllib.parse.unquote(raw)

if not code:
    print("Could not extract code. Make sure you pasted the full redirect URL.")
    exit(1)

# Step 2 — exchange code for access_token + refresh_token
credentials = base64.b64encode(f"{APP_ID}:{CERT_ID}".encode()).decode()
resp = requests.post(
    "https://api.ebay.com/identity/v1/oauth2/token",
    headers={
        "Authorization": f"Basic {credentials}",
        "Content-Type": "application/x-www-form-urlencoded",
    },
    data=(
        f"grant_type=authorization_code"
        f"&code={urllib.parse.quote(code)}"
        f"&redirect_uri={urllib.parse.quote(RU_NAME)}"
    ),
    timeout=15,
)

if resp.status_code != 200:
    print(f"\nToken exchange failed ({resp.status_code}):")
    print(resp.text)
    exit(1)

data = resp.json()
access_token  = data.get("access_token", "")
refresh_token = data.get("refresh_token", "")
expires_in    = data.get("expires_in", 7200)
rt_expires_in = data.get("refresh_token_expires_in", 0)

print("\n" + "="*60)
print("SUCCESS — tokens received:")
print(f"  access_token  expires in: {expires_in}s (~{expires_in//3600}h)")
print(f"  refresh_token expires in: {rt_expires_in}s (~{rt_expires_in//86400} days)")
print("="*60)

# Step 3 — write to .env
env_path = os.path.join(os.path.dirname(__file__), ".env")
with open(env_path, "r") as f:
    env_content = f.read()

def set_env_value(content, key, value):
    import re
    pattern = rf"^{key}=.*$"
    replacement = f"{key}={value}"
    if re.search(pattern, content, re.MULTILINE):
        return re.sub(pattern, replacement, content, flags=re.MULTILINE)
    return content + f"\n{key}={value}\n"

env_content = set_env_value(env_content, "EBAY_USER_TOKEN", access_token)
env_content = set_env_value(env_content, "EBAY_REFRESH_TOKEN", refresh_token)

with open(env_path, "w") as f:
    f.write(env_content)

print("\n.env updated with both tokens.")
print("The agent will now auto-refresh the access token — no manual updates needed for 18 months.")
