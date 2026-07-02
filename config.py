from dotenv import load_dotenv
import base64
import time
import requests
import os

load_dotenv()

# ---------------------------------------------------------------------------
# eBay Application Token (client credentials — for Browse API public searches)
# ---------------------------------------------------------------------------
# This is separate from the User Token. It uses App ID + Cert ID only, so it
# never requires user interaction and can be silently refreshed at any time.
# eBay's Browse API for public catalog searches requires this token, NOT the
# user token. User token is only for Sell/Order/Analytics APIs (seller-specific).

_app_token_cache = {"token": None, "expires_at": 0}


def get_app_token() -> str:
    """
    Returns a valid eBay Application OAuth token, refreshing if expired.
    Tokens last ~2 hours; cached in-process so each cycle only refreshes once.
    """
    if _app_token_cache["token"] and time.time() < _app_token_cache["expires_at"] - 60:
        return _app_token_cache["token"]

    credentials = base64.b64encode(f"{EBAY_APP_ID}:{EBAY_CERT_ID}".encode()).decode()
    try:
        resp = requests.post(
            "https://api.ebay.com/identity/v1/oauth2/token",
            headers={
                "Authorization": f"Basic {credentials}",
                "Content-Type": "application/x-www-form-urlencoded",
            },
            data="grant_type=client_credentials&scope=https%3A%2F%2Fapi.ebay.com%2Foauth%2Fapi_scope",
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
        _app_token_cache["token"] = data["access_token"]
        _app_token_cache["expires_at"] = time.time() + int(data.get("expires_in", 7200))
        return _app_token_cache["token"]
    except Exception as e:
        raise RuntimeError(f"Failed to obtain eBay Application token: {e}")

# --- eBay API ---
EBAY_APP_ID = os.getenv("EBAY_APP_ID")
EBAY_CERT_ID = os.getenv("EBAY_CERT_ID")
EBAY_DEV_ID = os.getenv("EBAY_DEV_ID")
EBAY_USER_TOKEN = os.getenv("EBAY_USER_TOKEN")        # current access token (short-lived, 2h)
EBAY_REFRESH_TOKEN = os.getenv("EBAY_REFRESH_TOKEN")  # long-lived refresh token (18 months)
EBAY_MARKETPLACE = "EBAY_GB"

# ---------------------------------------------------------------------------
# eBay User Token auto-refresh
# ---------------------------------------------------------------------------
# The access token (EBAY_USER_TOKEN) expires every 2 hours. Rather than
# updating .env manually each time, get_user_token() uses the refresh token
# (valid 18 months) to silently obtain a new access token when needed.
# Always call get_user_token() in Sell/Order/Analytics API headers — never
# use the raw EBAY_USER_TOKEN constant directly.

_user_token_cache = {
    "token": os.getenv("EBAY_USER_TOKEN"),  # seed from .env on startup
    "expires_at": 0,  # treat as expired immediately so refresh token is used on first call
}

_SELL_SCOPES = " ".join([
    "https://api.ebay.com/oauth/api_scope",
    "https://api.ebay.com/oauth/api_scope/sell.inventory",
    "https://api.ebay.com/oauth/api_scope/sell.fulfillment",
    "https://api.ebay.com/oauth/api_scope/sell.account",
    "https://api.ebay.com/oauth/api_scope/sell.analytics.readonly",
])


def get_user_token() -> str:
    """
    Returns a valid eBay User OAuth token for Sell/Order/Account APIs.
    Uses the refresh token to silently renew the access token when it
    expires — no manual action needed for 18 months.

    Falls back to the raw EBAY_USER_TOKEN if no refresh token is configured
    (e.g. during initial setup before EBAY_REFRESH_TOKEN is added to .env).
    """
    if _user_token_cache["token"] and time.time() < _user_token_cache["expires_at"] - 120:
        return _user_token_cache["token"]

    if not EBAY_REFRESH_TOKEN:
        # No refresh token yet — use raw token and warn
        if EBAY_USER_TOKEN:
            return EBAY_USER_TOKEN
        raise RuntimeError(
            "EBAY_USER_TOKEN is empty and EBAY_REFRESH_TOKEN is not set. "
            "Generate a new token at developer.ebay.com and add EBAY_REFRESH_TOKEN to .env."
        )

    credentials = base64.b64encode(f"{EBAY_APP_ID}:{EBAY_CERT_ID}".encode()).decode()
    import urllib.parse
    try:
        resp = requests.post(
            "https://api.ebay.com/identity/v1/oauth2/token",
            headers={
                "Authorization": f"Basic {credentials}",
                "Content-Type": "application/x-www-form-urlencoded",
            },
            data=(
                f"grant_type=refresh_token"
                f"&refresh_token={urllib.parse.quote(EBAY_REFRESH_TOKEN)}"
                f"&scope={urllib.parse.quote(_SELL_SCOPES)}"
            ),
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
        _user_token_cache["token"] = data["access_token"]
        _user_token_cache["expires_at"] = time.time() + int(data.get("expires_in", 7200))
        return _user_token_cache["token"]
    except Exception as e:
        # Refresh failed — fall back to last known token and warn
        import logging
        logging.getLogger(__name__).error(f"eBay user token refresh failed: {e} — using cached token")
        if _user_token_cache["token"]:
            return _user_token_cache["token"]
        raise RuntimeError(f"eBay user token refresh failed and no cached token available: {e}")

EBAY_BROWSE_BASE = "https://api.ebay.com/buy/browse/v1"
EBAY_SELL_BASE = "https://api.ebay.com/sell/inventory/v1"
EBAY_ORDER_BASE = "https://api.ebay.com/sell/fulfillment/v1"
EBAY_ANALYTICS_BASE = "https://api.ebay.com/sell/analytics/v1"

# Business policies (Seller Hub → Account → Business Policies).
# Fetched once via Account API and pinned here — re-fetch if policies change.
EBAY_FULFILLMENT_POLICY_ID = os.getenv("EBAY_FULFILLMENT_POLICY_ID", "278066573018")  # "Postage policy" — free shipping, 2 day handling
EBAY_PAYMENT_POLICY_ID = os.getenv("EBAY_PAYMENT_POLICY_ID", "278111808018")          # "eBay Managed Payments"
EBAY_RETURN_POLICY_ID = os.getenv("EBAY_RETURN_POLICY_ID", "278066546018")            # "Return Policy" — 30 day returns

# Furniture (2-man delivery via supplier, no API/tracking integration) needs longer
# handling time than small-parcel items — the supplier's own process takes ~3 days
# before dispatch even starts, plus the manual email-order step in supplier.py.
EBAY_FURNITURE_FULFILLMENT_POLICY_ID = os.getenv("EBAY_FURNITURE_FULFILLMENT_POLICY_ID", "280390450018")  # "Furniture 2-Man Delivery" — 3 day handling

# CJ Dropshipping ships from China (4-7+ day real transit via YunExpress/
# CJPacket) — needs longer handling time than the UK small-parcel default
# so eBay's delivery estimate doesn't promise something CJ can't hit.
EBAY_CJ_FULFILLMENT_POLICY_ID = os.getenv("EBAY_CJ_FULFILLMENT_POLICY_ID", "280397104018")  # "CJ Dropship International" — 5 day handling

# --- Claude API ---
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")

# --- Supplier API keys ---
AVASAM_CONSUMER_KEY = os.getenv("AVASAM_CONSUMER_KEY")
AVASAM_SECRET_KEY   = os.getenv("AVASAM_SECRET_KEY")

# ---------------------------------------------------------------------------
# Avasam access token (consumer_key + secret_key → short-lived access token)
# ---------------------------------------------------------------------------
_avasam_token_cache = {"token": None, "expires_at": 0}


def get_avasam_token() -> str | None:
    """
    Exchange Avasam consumer/secret keys for an access token.
    Cached until expiry. Returns None if keys are not configured.
    """
    if not AVASAM_CONSUMER_KEY or not AVASAM_SECRET_KEY:
        return None
    if _avasam_token_cache["token"] and time.time() < _avasam_token_cache["expires_at"] - 60:
        return _avasam_token_cache["token"]
    try:
        resp = requests.post(
            "https://app.avasam.com/api/auth/request-token",
            json={"consumer_key": AVASAM_CONSUMER_KEY, "secret_key": AVASAM_SECRET_KEY},
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
        _avasam_token_cache["token"] = data["access_token"]
        # expires_at from Avasam is an ISO string e.g. "2026-06-17T19:34:43.056Z"
        # Convert to Unix timestamp for comparison with time.time()
        raw_exp = data.get("expires_at")
        if raw_exp:
            from datetime import datetime, timezone
            dt = datetime.fromisoformat(raw_exp.replace("Z", "+00:00"))
            _avasam_token_cache["expires_at"] = dt.timestamp()
        else:
            _avasam_token_cache["expires_at"] = time.time() + 3600
        return _avasam_token_cache["token"]
    except Exception as e:
        import logging
        logging.getLogger(__name__).error(f"Avasam token request failed: {e}")
        return None
COSTWAY_API_KEY = os.getenv("COSTWAY_API_KEY")
BIGBUY_API_KEY = os.getenv("BIGBUY_API_KEY")
CJ_EMAIL = os.getenv("CJ_EMAIL")
CJ_API_KEY = os.getenv("CJ_API_KEY")
WHOLESALE2B_API_KEY = os.getenv("WHOLESALE2B_API_KEY")

# --- Notifications ---
ALERT_EMAIL = os.getenv("ALERT_EMAIL", "07487863927n@gmail.com")
SMTP_HOST = os.getenv("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER = os.getenv("SMTP_USER")
SMTP_PASS = os.getenv("SMTP_PASS")

# --- Product filters ---
# Fallback price band used only if a category doesn't define its own
# min_price/max_price (see CATEGORIES below) — most categories should set
# their own, since "high value" furniture and "viral cheap gadget" items
# need very different bands.
MIN_PRICE_GBP = 50.0
MAX_PRICE_GBP = 600.0
MAX_ACTIVE_COMPETITORS = 25000  # max active identical listings on eBay UK
MIN_NET_MARGIN_PCT = 25.0       # minimum profit margin after all fees

# Declared listing quantity for dropshipped (not physically held) stock.
# Kept low on a new account — a brand-new seller showing 50 units of
# something they don't hold is a worse inventory-vs-profile match than a
# modest number, and caps how many simultaneous orders could go wrong if
# the supplier hits a stock-out or delay. Raise gradually as the account
# builds sales history (mirrors the listings/day ramp in main.py).
DEFAULT_LISTING_QUANTITY = int(os.getenv("DEFAULT_LISTING_QUANTITY", "5"))

# --- eBay fee model (UK) ---
# Final value fee varies by category — some are flat %, some tiered by sale
# amount. Source: eBay "Fees for sellers" page (full category table).
# Each entry is a list of (upper_bound_gbp, rate) tiers; a flat-rate category
# is just a single tier with upper_bound = inf.
EBAY_FVF_TIERS_BY_CATEGORY = {
    "antiques":                      [(float("inf"), 0.109)],
    "art":                           [(float("inf"), 0.109)],
    "baby":                         [(float("inf"), 0.109)],
    "books_comics_magazines":        [(float("inf"), 0.099)],
    "business_office_industrial":    [(float("inf"), 0.125)],
    "cameras_photography":           [(float("inf"), 0.099)],
    "clothes_shoes_accessories":     [(float("inf"), 0.119)],
    "coins":                         [(450, 0.109), (float("inf"), 0.03)],
    "collectables":                  [(float("inf"), 0.109)],
    "computers_tablets_networking":  [(float("inf"), 0.099)],
    "crafts":                        [(float("inf"), 0.129)],
    "dolls_bears":                   [(float("inf"), 0.109)],
    "event_tickets":                 [(float("inf"), 0.129)],
    "films_tv":                      [(float("inf"), 0.099)],
    "garden_patio":                  [(float("inf"), 0.109)],
    "health_beauty":                 [(float("inf"), 0.109)],
    "holidays_travel":               [(650, 0.079), (float("inf"), 0.03)],
    "home_furniture_diy":            [(500, 0.119), (float("inf"), 0.079)],
    "furniture":                     [(500, 0.109), (1000, 0.079), (float("inf"), 0.03)],
    "appliances_diy_tools":          [(400, 0.069), (float("inf"), 0.03)],
    "jewellery_watches":             [(1000, 0.149), (float("inf"), 0.04)],
    "watches":                       [(750, 0.129), (float("inf"), 0.03)],
    "mobile_phones_communication":   [(float("inf"), 0.099)],
    "mobile_smart_phones":           [(1000, 0.069), (float("inf"), 0.03)],
    "music":                         [(float("inf"), 0.099)],
    "musical_instruments_dj":        [(float("inf"), 0.109)],
    "pet_supplies":                  [(float("inf"), 0.129)],
    "pottery_ceramics_glass":        [(float("inf"), 0.109)],
    "sound_vision":                  [(float("inf"), 0.099)],
    "sporting_goods":                [(float("inf"), 0.109)],
    "sports_memorabilia":            [(float("inf"), 0.109)],
    "stamps":                        [(float("inf"), 0.109)],
    "toys_games":                    [(float("inf"), 0.109)],
    "vehicle_parts_accessories":     [(750, 0.095), (float("inf"), 0.03)],
    "video_games_consoles":          [(float("inf"), 0.099)],
    "wholesale_job_lots":            [(float("inf"), 0.129)],
    "everything_else":               [(float("inf"), 0.129)],  # fallback default
}
EBAY_REGULATORY_FEE_PCT = 0.0035   # 0.35% on total sale amount, all categories
EBAY_PER_ORDER_FEE_LOW = 0.30      # orders <= £10
EBAY_PER_ORDER_FEE_HIGH = 0.40     # orders > £10
EBAY_PER_ORDER_THRESHOLD_GBP = 10.0

# --- VAT ---
# Seller is NOT VAT registered: trade prices and delivery charges from
# suppliers are paid VAT-inclusive with no input VAT reclaim.
VAT_REGISTERED = False
VAT_RATE = 0.20


def calculate_ebay_fees(sell_price_gbp: float, category_key: str = "furniture") -> float:
    """
    Total eBay UK fees for a sale: category-specific tiered final value fee
    + 0.35% regulatory operating fee + per-order fee. Single source of
    truth — used by both supplier.py (margin pre-check) and lister.py
    (final pricing).

    category_key must be one of EBAY_FVF_TIERS_BY_CATEGORY's keys; unknown
    keys fall back to "everything_else" (12.9% flat) rather than raising,
    since a missing category shouldn't crash a research pass.
    """
    tiers = EBAY_FVF_TIERS_BY_CATEGORY.get(category_key, EBAY_FVF_TIERS_BY_CATEGORY["everything_else"])

    remaining = sell_price_gbp
    lower_bound = 0.0
    fvf = 0.0
    for upper_bound, rate in tiers:
        tier_amount = min(remaining, upper_bound - lower_bound)
        if tier_amount <= 0:
            break
        fvf += tier_amount * rate
        remaining -= tier_amount
        lower_bound = upper_bound

    regulatory_fee = sell_price_gbp * EBAY_REGULATORY_FEE_PCT
    per_order_fee = (
        EBAY_PER_ORDER_FEE_LOW if sell_price_gbp <= EBAY_PER_ORDER_THRESHOLD_GBP
        else EBAY_PER_ORDER_FEE_HIGH
    )
    return fvf + regulatory_fee + per_order_fee


def total_cost_with_vat(cost_gbp: float, shipping_gbp: float) -> float:
    """Supplier cost + delivery, inclusive of VAT if not VAT-registered."""
    total = cost_gbp + shipping_gbp
    return total * (1 + VAT_RATE) if not VAT_REGISTERED else total

# --- Volume caps (listings per day) ---
DAILY_LISTING_CAP = int(os.getenv("DAILY_LISTING_CAP", "10"))  # overridden by .env

# --- Account health alert thresholds ---
MAX_DEFECT_RATE = 0.01          # 1%
MAX_LATE_DISPATCH_RATE = 0.03   # 3%
MAX_OPEN_CASES = 1

# --- Scheduler times (24h, London time) ---
RESEARCH_HOUR = 6        # Morning cycle — catches overnight demand signals
RESEARCH_HOUR_EVENING = 18  # Evening cycle — new products added by CJ/suppliers during the day
HEALTH_CHECK_HOUR = 8
ORDER_POLL_MINUTES = 15
SUPPLIER_TRACKING_HOURS = 4

# --- Category config ---
# eBay UK category IDs. min_price/max_price are per-category since "high
# value furniture" and "viral cheap gadget" need very different bands —
# a single global £50-£600 band was silently hiding real competition data
# for anything cheaper (see MIN_PRICE_GBP/MAX_PRICE_GBP fallback above).
CATEGORIES = {
    "furniture":          {"id": "175758", "weight": 1.5, "fee_key": "furniture",
                            "min_price": 50.0, "max_price": 600.0},
    "mattress":           {"id": "131588", "weight": 1.4, "fee_key": "furniture",
                            "min_price": 60.0, "max_price": 400.0},
    # Non-furniture ids below are PARENT categories (used only for eBay Browse searches in research.py).
    # lister.py uses the Taxonomy API to resolve a leaf_id per product title at listing time.
    # leaf_id is the fallback leaf category when the Taxonomy API fails.
    "home_appliances":    {"id": "20713", "weight": 1.2, "fee_key": "appliances_diy_tools",
                            "min_price": 20.0, "max_price": 400.0,
                            "leaf_id": "133705"},   # Kettles
    "tools_hardware":     {"id": "631",   "weight": 1.1, "fee_key": "appliances_diy_tools",
                            "min_price": 10.0, "max_price": 300.0,
                            "leaf_id": "147809"},   # Laser Measurers
    "auto_parts":         {"id": "6030",  "weight": 1.1, "fee_key": "vehicle_parts_accessories",
                            "min_price": 10.0, "max_price": 300.0,
                            "leaf_id": "179476"},   # Code Readers & Scanners
    "sports_fitness":     {"id": "888",   "weight": 1.0, "fee_key": "sporting_goods",
                            "min_price": 10.0, "max_price": 300.0,
                            "leaf_id": "79759"},    # Resistance Bands & Expanders
    "health_devices":     {"id": "67588", "weight": 1.0, "fee_key": "health_beauty",
                            "min_price": 5.0,  "max_price": 150.0,
                            "leaf_id": "36449"},    # Massagers
    "garden_outdoor":     {"id": "159912","weight": 1.0, "fee_key": "garden_patio",
                            "min_price": 15.0, "max_price": 400.0,
                            "leaf_id": "20509"},    # Garden Lighting Accessories
    "home_living":        {"id": "11700",  "weight": 1.1, "fee_key": "everything_else",
                            "min_price": 8.0,  "max_price": 200.0,
                            "leaf_id": "66861"},    # Home Decor
    "tech_accessories":   {"id": "58058",  "weight": 1.2, "fee_key": "everything_else",
                            "min_price": 8.0,  "max_price": 200.0,
                            "leaf_id": "80053"},    # Phone Cases & Covers
    "pet_supplies":       {"id": "1281",   "weight": 1.0, "fee_key": "everything_else",
                            "min_price": 8.0,  "max_price": 150.0,
                            "leaf_id": "46655"},    # Dog Beds
    "baby_kids":          {"id": "2",      "weight": 1.0, "fee_key": "everything_else",
                            "min_price": 8.0,  "max_price": 150.0,
                            "leaf_id": "19169"},    # Baby Monitors
    "office_stationery":  {"id": "26395",  "weight": 1.0, "fee_key": "everything_else",
                            "min_price": 8.0,  "max_price": 150.0,
                            "leaf_id": "26395"},    # Office Equipment
}

# VeRO brand keywords to skip (prevents listing violations).
# Checked at research time AND again at listing time against both the
# supplier title and the Claude-generated title.
VERO_BRANDS = [
    # Electronics
    "apple", "samsung", "sony", "lg", "panasonic", "philips", "bose", "beats",
    "jbl", "harman", "canon", "nikon", "fujifilm", "gopro", "garmin",
    "fitbit", "microsoft", "intel", "nvidia", "amd",
    # Home appliances
    "dyson", "hoover", "miele", "bosch", "siemens", "hotpoint", "whirlpool",
    "delonghi", "nespresso", "kenwood", "kitchenaid", "smeg", "aga",
    # Power tools
    "makita", "dewalt", "stanley", "milwaukee", "hilti", "festool", "metabo",
    "black and decker", "black+decker", "ryobi", "worx", "snap-on",
    # Fashion / luxury
    "nike", "adidas", "puma", "reebok", "new balance", "under armour",
    "gucci", "louis vuitton", "chanel", "hermes", "prada", "burberry",
    "rolex", "omega", "tag heuer", "cartier", "pandora", "swarovski",
    "north face", "columbia", "patagonia", "ugg", "timberland", "vans",
    "converse", "dr martens", "hunter",
    # Toys / media
    "lego", "disney", "marvel", "pokemon", "nintendo", "playstation",
    "xbox", "hasbro", "mattel", "fisher-price",
    # Automotive brands
    "bmw", "mercedes", "audi", "volkswagen", "ford", "vauxhall", "honda",
    # Other common VeRO members
    "zippo", "montblanc", "ray-ban", "oakley", "fossil", "leatherman",
    "victorinox", "swiss army",
]

# Supplier priority order (1 = try first)
SUPPLIER_PRIORITY = ["wholesalebeds", "avasam", "costway", "wholesale_domestic", "birlea", "bigbuy", "cj"]

# Furniture must only come from UK-warehouse suppliers
FURNITURE_ALLOWED_SUPPLIERS = ["wholesalebeds", "avasam", "costway", "wholesale_domestic", "birlea"]
