"""
Creates eBay UK listings via the official eBay Sell API.
Uses Claude API to generate optimized titles and descriptions.
"""

import hashlib
import logging
import json
import math
import os
import re
import requests
import anthropic
from config import (
    EBAY_SELL_BASE, ANTHROPIC_API_KEY, get_user_token, get_app_token,
    EBAY_FULFILLMENT_POLICY_ID, EBAY_FURNITURE_FULFILLMENT_POLICY_ID, EBAY_CJ_FULFILLMENT_POLICY_ID,
    EBAY_PAYMENT_POLICY_ID, EBAY_RETURN_POLICY_ID,
    DAILY_LISTING_CAP, CATEGORIES, VERO_BRANDS, MIN_NET_MARGIN_PCT, DEFAULT_LISTING_QUANTITY,
    calculate_ebay_fees, total_cost_with_vat,
)
from db import insert_product, count_listings_today

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# eBay Taxonomy — leaf category resolver
# ---------------------------------------------------------------------------
# eBay's Sell Inventory API only accepts LEAF categories for listings (error 25005
# otherwise). Our research categories are parent categories used for Browse searches.
# This function resolves a leaf category per product using the Taxonomy API.

_category_suggestion_cache: dict[str, str] = {}

# Verified eBay UK leaf category IDs for our most common product types.
# Checked before calling the Taxonomy API, which occasionally returns non-leaf IDs.
_KEYWORD_LEAF_CATEGORY: dict[str, str] = {
    "ottoman":            "175758",   # Beds & Headboards (proven — MONTOTTO listed successfully)
    "storage bed":        "175758",
    "eye massager":       "36449",    # Body Massagers
    "massage gun":        "36449",
    "tens machine":       "36449",
    "foot massager":      "36449",
    "massager":           "36449",
    "electric kettle":    "133705",   # Kettles
    "kettle":             "133705",
    "laser measure":      "147809",   # Laser Measurers
    "obd2":               "179476",   # Code Readers & Scanners
    "diagnostic scanner": "179476",
    "resistance band":    "79759",    # Resistance Bands & Expanders
    "jump rope":          "79759",
    "pull up bar":        "79759",
}


def _suggest_ebay_category(title: str, fallback_leaf_id: str) -> str:
    """
    Return a leaf eBay UK category ID for the given product title.
    Resolution order: keyword map → Taxonomy API → fallback_leaf_id from config.
    Results cached in-process to avoid repeated API calls per keyword.
    """
    title_lower = title.lower()
    for kw, leaf_id in _KEYWORD_LEAF_CATEGORY.items():
        if kw in title_lower:
            log.debug(f"Category from keyword map: '{kw}' → {leaf_id}")
            return leaf_id

    key = title[:80]
    if key in _category_suggestion_cache:
        return _category_suggestion_cache[key]
    try:
        r = requests.get(
            "https://api.ebay.com/commerce/taxonomy/v1/category_tree/3/get_category_suggestions",
            headers={"Authorization": f"Bearer {get_app_token()}",
                     "X-EBAY-C-MARKETPLACE-ID": "EBAY_GB", "Accept": "application/json"},
            params={"q": key},
            timeout=10,
        )
        suggestions = r.json().get("categorySuggestions", [])
        if suggestions:
            cat_id = suggestions[0]["category"]["categoryId"]
            cat_name = suggestions[0]["category"]["categoryName"]
            log.info(f"Category suggestion: '{key[:45]}' → {cat_id} ({cat_name})")
            _category_suggestion_cache[key] = cat_id
            return cat_id
    except Exception as e:
        log.warning(f"Category suggestion failed for '{key[:40]}': {e}")
    return fallback_leaf_id

def _sell_headers() -> dict:
    return {
        "Authorization": f"Bearer {get_user_token()}",
        "X-EBAY-C-MARKETPLACE-ID": "EBAY_GB",
        "Content-Language": "en-GB",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }

_claude = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)


# ---------------------------------------------------------------------------
# VeRO guard — second check at listing time
# ---------------------------------------------------------------------------

def _vero_blocked(text: str) -> bool:
    """Return True if any VeRO brand keyword appears in the text."""
    lower = text.lower()
    return any(brand in lower for brand in VERO_BRANDS)


def _first_vero_hit(text: str) -> str:
    lower = text.lower()
    for brand in VERO_BRANDS:
        if brand in lower:
            return brand
    return ""


# ---------------------------------------------------------------------------
# Content generation
# ---------------------------------------------------------------------------

def _generate_listing_content(keyword: str, supplier_title: str, description: str, category: str,
                               competitor_titles: list[str] | None = None,
                               avg_price_gbp: float | None = None,
                               our_price_gbp: float | None = None) -> dict:
    """Use Claude to write an optimised eBay title (≤80 chars) + description."""
    comp_block = ""
    if competitor_titles:
        comp_list = "\n".join(f"  - {t}" for t in competitor_titles[:8])
        comp_block = f"""
Real titles currently ranking on eBay UK for this search (these are already
getting clicks — mine them for the exact keyword variants, synonyms and
word order buyers actually search, don't just copy one):
{comp_list}
"""

    price_block = ""
    if avg_price_gbp and our_price_gbp:
        if our_price_gbp < avg_price_gbp:
            price_block = f"\nWe're pricing at £{our_price_gbp} vs a market average of £{avg_price_gbp} — it's fine to let good value come through naturally, but don't invent discount percentages or claim a 'was' price that isn't real."
        price_block += f"\nOur price: £{our_price_gbp}. Market average: £{avg_price_gbp}."

    prompt = f"""You are writing an eBay UK product listing for a dropship item. The
goal is maximum organic visibility in eBay's Cassini search algorithm and a
high click-through/conversion rate.

Product: {supplier_title}
Search keyword: {keyword}
Category: {category}
Supplier description: {description[:500] if description else 'Not provided'}
{comp_block}{price_block}

=== TITLE RULES (max 80 characters) ===
- The first 40 characters are what buyers see on mobile — put the single
  most-searched phrase there (the exact term a buyer types, e.g.
  "Ottoman Storage Bed Double Grey Fabric").
- After the primary phrase, pack in secondary keywords: size, colour/color
  (use BOTH British and American spelling where applicable — "grey/gray",
  "colour/color" — eBay indexes both and doubles search reach), material,
  key feature, use-case.
- Include item-specific signals Cassini weighs: size (Double/King/4ft6),
  colour, material (Fabric/Velvet/Wood), style (Ottoman/Gas Lift/Divan).
- Use every character you reasonably can — unused title length is wasted
  search surface. Aim for 75–80 chars.
- BANNED words (eBay policy + wasted space): "new", "free", "best",
  "high quality", "amazing", "great", "stunning", "gorgeous", "perfect",
  ALL CAPS words, symbols (!, *, #).

=== DESCRIPTION RULES (150–250 words, HTML) ===
- First sentence must contain the exact primary keyword phrase from the
  title — eBay's indexer weights the opening of the description.
- Follow with a 1-sentence buyer benefit hook (what problem this solves).
- Bullet points for key features, each as a buyer benefit
  (e.g. "Gas-lift mechanism — effortless access to the full-depth storage
  below, no heavy lifting").
- Weave in 2–3 keyword variants from the competitor titles naturally in the
  body (helps long-tail ranking without stuffing).
- Close with a brief reassurance line: delivery speed or return policy
  (builds buyer confidence, reduces cart abandonment).
- No invented specs, no fabricated certifications, no false urgency
  ("only 2 left!") — these are policy violations and trust risks.

Respond in JSON exactly:
{{"title": "...", "description": "..."}}"""

    message = _claude.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=512,
        messages=[{"role": "user", "content": prompt}],
    )
    text = message.content[0].text.strip()
    # Strip markdown code fences if present
    if text.startswith("```"):
        text = text.split("```")[1]
        if text.startswith("json"):
            text = text[4:]
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        log.warning("Claude returned non-JSON; using fallback content")
        _banned = ["new", "free", "best", "best-selling", "best selling",
                   "high quality", "amazing", "great", "stunning", "gorgeous", "perfect"]
        fallback_title = supplier_title
        for word in _banned:
            fallback_title = re.sub(rf'\b{re.escape(word)}\b', '', fallback_title, flags=re.IGNORECASE).strip()
        fallback_title = " ".join(fallback_title.split())  # collapse multiple spaces
        return {"title": fallback_title[:80], "description": description or supplier_title}


# ---------------------------------------------------------------------------
# Pricing
# ---------------------------------------------------------------------------

def _calculate_sell_price(cost_gbp: float, shipping_gbp: float, target_margin: float = MIN_NET_MARGIN_PCT / 100,
                           fee_key: str = "everything_else") -> float:
    """
    Find the lowest sell price that clears target_margin after real eBay
    fees and VAT-inclusive supplier costs. The tiered final value fee has
    no closed-form inverse, so this searches upward in 50p steps.

    fee_key: which EBAY_FVF_TIERS_BY_CATEGORY entry applies to this listing's
             eBay category — defaults to the 12.9% flat fallback if unknown.
    """
    total_cost = total_cost_with_vat(cost_gbp, shipping_gbp)

    sell = total_cost * 1.2
    for _ in range(2000):
        fees = calculate_ebay_fees(sell, fee_key)
        net = sell - fees
        margin = (net - total_cost) / sell if sell > 0 else -1
        if margin >= target_margin:
            break
        sell += 0.50

    # Round up to nearest £X.99 — a flat £0.99-granularity round avoids the
    # overshoot a coarser "nearest £X9" step caused on cheap items (e.g. a
    # £10.02 and £16.08 minimum both rounding up to the same £19).
    sell = math.ceil(sell) - 0.01
    return max(sell, total_cost * 1.35)  # safety floor


# ---------------------------------------------------------------------------
# eBay Inventory API — create listing
# ---------------------------------------------------------------------------

def _get_or_create_inventory_item(sku: str, title: str, description: str,
                                   images: list[str], category_id: str,
                                   aspects: dict[str, list[str]] | None = None) -> bool:
    """
    Create/update an inventory item via eBay Inventory API.

    aspects: required item specifics for the category (e.g. {"Brand": ["Unbranded"],
             "Type": ["Detangling Comb"]}) — look these up once per category via
             Taxonomy API's get_item_aspects_for_category and reuse. Without
             required aspects, publish fails with a category-specific error.
    """
    url = f"https://api.ebay.com/sell/inventory/v1/inventory_item/{sku}"
    product_payload = {
        "title": title[:80],
        "description": description,
        "imageUrls": [img for img in images if img][:6],
    }
    if aspects:
        product_payload["aspects"] = aspects

    payload = {
        "availability": {
            "shipToLocationAvailability": {"quantity": DEFAULT_LISTING_QUANTITY}
        },
        "condition": "NEW",
        "product": product_payload,
    }
    resp = requests.put(url, headers=_sell_headers(), json=payload, timeout=15)
    if resp.status_code in (200, 201, 204):
        return True
    log.error(f"Inventory item create failed [{resp.status_code}]: {resp.text[:200]}")
    return False


def _find_existing_offer(sku: str) -> str | None:
    """Check if an offer already exists for this SKU to avoid creating a duplicate."""
    url = "https://api.ebay.com/sell/inventory/v1/offer"
    params = {"sku": sku, "marketplace_id": "EBAY_GB"}
    resp = requests.get(url, headers=_sell_headers(), params=params, timeout=15)
    if resp.status_code == 200:
        offers = resp.json().get("offers", [])
        if offers:
            return offers[0].get("offerId")
    return None


def _update_offer(offer_id: str, sku: str, price_gbp: float, category_id: str,
                   is_furniture: bool = False, supplier: str = "", uk_warehouse: bool = True) -> bool:
    """
    Refresh an existing unpublished offer via PUT — stamps it with the current
    category_id and price. Prevents stale offers (created in previous failed runs
    with a bad category) from being reused unchanged, which causes error 25005 forever.
    """
    if is_furniture:
        fulfillment_policy_id = EBAY_FURNITURE_FULFILLMENT_POLICY_ID
    elif supplier == "cj" and not uk_warehouse:
        fulfillment_policy_id = EBAY_CJ_FULFILLMENT_POLICY_ID
    else:
        fulfillment_policy_id = EBAY_FULFILLMENT_POLICY_ID

    url = f"{EBAY_SELL_BASE}/offer/{offer_id}"
    payload = {
        "sku": sku,
        "marketplaceId": "EBAY_GB",
        "format": "FIXED_PRICE",
        "availableQuantity": DEFAULT_LISTING_QUANTITY,
        "categoryId": category_id,
        "pricingSummary": {
            "price": {"value": str(round(price_gbp, 2)), "currency": "GBP"}
        },
        "listingPolicies": {
            "fulfillmentPolicyId": fulfillment_policy_id,
            "paymentPolicyId": EBAY_PAYMENT_POLICY_ID,
            "returnPolicyId": EBAY_RETURN_POLICY_ID,
        },
        "merchantLocationKey": "default",
    }
    resp = requests.put(url, headers=_sell_headers(), json=payload, timeout=15)
    if resp.status_code in (200, 204):
        log.info(f"Updated offer {offer_id} → category {category_id} at £{price_gbp}")
        return True
    log.warning(f"Offer update failed [{resp.status_code}]: {resp.text[:200]}")
    return False


def _delete_offer(offer_id: str) -> bool:
    """Delete a stale unpublished offer so it can be recreated with a correct category."""
    resp = requests.delete(
        f"{EBAY_SELL_BASE}/offer/{offer_id}",
        headers=_sell_headers(), timeout=15,
    )
    if resp.status_code in (200, 204):
        log.info(f"Deleted stale offer {offer_id}")
        return True
    log.warning(f"Offer delete failed [{resp.status_code}]: {resp.text[:200]}")
    return False


def _create_offer(sku: str, price_gbp: float, category_id: str, is_furniture: bool = False,
                   supplier: str = "", uk_warehouse: bool = True) -> str | None:
    """Create an eBay offer for an inventory item. Returns offer_id or None."""
    existing = _find_existing_offer(sku)
    if existing:
        log.info(f"Offer already exists for SKU {sku} — updating category/price before reuse")
        _update_offer(existing, sku, price_gbp, category_id,
                      is_furniture=is_furniture, supplier=supplier, uk_warehouse=uk_warehouse)
        return existing

    if is_furniture:
        fulfillment_policy_id = EBAY_FURNITURE_FULFILLMENT_POLICY_ID
    elif supplier == "cj" and not uk_warehouse:
        # CJ shipping from China — longer handling window.
        # This is a safety net only: _search_cj now blocks non-UK stock
        # at search time, so uk_warehouse should always be True for CJ results.
        fulfillment_policy_id = EBAY_CJ_FULFILLMENT_POLICY_ID
    else:
        fulfillment_policy_id = EBAY_FULFILLMENT_POLICY_ID

    url = "https://api.ebay.com/sell/inventory/v1/offer"
    payload = {
        "sku": sku,
        "marketplaceId": "EBAY_GB",
        "format": "FIXED_PRICE",
        "availableQuantity": DEFAULT_LISTING_QUANTITY,
        "categoryId": category_id,
        "pricingSummary": {
            "price": {"value": str(round(price_gbp, 2)), "currency": "GBP"}
        },
        "listingPolicies": {
            "fulfillmentPolicyId": fulfillment_policy_id,
            "paymentPolicyId": EBAY_PAYMENT_POLICY_ID,
            "returnPolicyId": EBAY_RETURN_POLICY_ID,
        },
        "merchantLocationKey": "default",
    }
    resp = requests.post(url, headers=_sell_headers(), json=payload, timeout=15)
    if resp.status_code == 201:
        return resp.json().get("offerId")
    log.error(f"Offer create failed [{resp.status_code}]: {resp.text[:200]}")
    return None


def _publish_offer(offer_id: str) -> str | None:
    """
    Publish offer → creates live eBay listing.
    Returns listing_id on success, "25005" on non-leaf category error, None on other failures.
    """
    url = f"{EBAY_SELL_BASE}/offer/{offer_id}/publish"
    resp = requests.post(url, headers=_sell_headers(), json={}, timeout=15)
    if resp.status_code == 200:
        return resp.json().get("listingId")
    try:
        errors = resp.json().get("errors", [])
        if any(e.get("errorId") == 25005 for e in errors):
            log.warning(f"Offer {offer_id}: category not a leaf — will delete and retry with fallback")
            return "25005"
    except Exception:
        pass
    log.error(f"Publish offer failed [{resp.status_code}]: {resp.text[:600]}")
    return None


# ---------------------------------------------------------------------------
# Item aspects builder (required item specifics per category)
# ---------------------------------------------------------------------------

_MATTRESS_SIZE_MAP = {
    "super king": "Super King", "6'0": "Super King", "6ft": "Super King",
    "king":       "King",       "5'0": "King",       "5ft": "King",
    "double":     "Double",     "4'6": "Double",     "4ft6": "Double",
    "small double": "Small Double", "4'0": "Small Double",
    "single":     "Single",     "3'0": "Single",     "3ft": "Single",
}

_COLOUR_KEYWORDS = {
    "grey": "Grey", "gray": "Grey", "black": "Black", "white": "White",
    "cream": "Cream", "beige": "Beige", "brown": "Brown", "oatmeal": "Beige",
    "velvet": None,  # velvet isn't a colour — colour extracted elsewhere
    "fabric": None,
}

def _build_aspects(category: str, supplier_match: dict, title: str) -> dict:
    """
    Build eBay required item specifics for a category.
    Category 175758 (Beds & Headboards) requires: Type, Frame Material, Compatible Mattress Size.
    Category 131588 (Mattresses) requires: Firmness, Size, Type.
    """
    aspects: dict[str, list[str]] = {}
    product_title = (supplier_match.get("title") or title).lower()

    if category == "furniture":
        aspects["Type"] = ["Storage Bed"]

        mattress_size = None
        for kw, mapped in _MATTRESS_SIZE_MAP.items():
            if kw in product_title:
                mattress_size = mapped
                break
        aspects["Compatible Mattress Size"] = [mattress_size or "Double"]
        aspects["Frame Material"] = ["MDF"]

        colour = None
        for kw, mapped in _COLOUR_KEYWORDS.items():
            if kw in product_title and mapped:
                colour = mapped
                break
        if colour:
            aspects["Colour"] = [colour]

        aspects["Brand"] = ["Unbranded"]
        aspects["Mattress Included"] = ["No"]
        aspects["Assembly Required"] = ["Yes"]

    elif category == "mattress":
        # Required: Firmness, Size, Type (eBay category 131588)
        size = None
        for kw, mapped in _MATTRESS_SIZE_MAP.items():
            if kw in product_title:
                size = mapped
                break
        aspects["Size"] = [size or "Double"]

        if "pocket" in product_title or "sprung" in product_title or "spring" in product_title:
            aspects["Type"] = ["Innerspring"]
        elif "memory" in product_title:
            aspects["Type"] = ["Memory Foam"]
        else:
            aspects["Type"] = ["Open Spring"]

        if "ortho" in product_title or "extra firm" in product_title:
            aspects["Firmness"] = ["Firm"]
        elif "3000" in product_title or "2000" in product_title or "luxury" in product_title:
            aspects["Firmness"] = ["Medium Firm"]
        else:
            aspects["Firmness"] = ["Medium"]

        aspects["Brand"] = ["Unbranded"]

    return aspects


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

_PAUSE_FLAG = os.path.join(os.path.dirname(os.path.abspath(__file__)), "PAUSE_LISTINGS")


def create_listing(opportunity: dict, supplier_match: dict, dry_run: bool = False,
                    publish: bool = True, target_sell_price_gbp: float | None = None) -> str | None:
    """
    Full pipeline: generate content → price → create inventory item → offer → publish.
    Returns eBay listing ID (or offer ID if publish=False) on success, None on failure.

    opportunity: dict from research.find_opportunities(). May include an
                 "aspects" key ({"Brand": ["Unbranded"], ...}) — required
                 item specifics for the category, looked up once via
                 Taxonomy API's get_item_aspects_for_category. Missing
                 required aspects make publish fail with a category error.
    supplier_match: dict from supplier.find_best_supplier()
    dry_run: if True, print the listing details but do NOT submit to eBay at all
    publish: if False, create the real inventory item + offer on eBay but stop
             before publishing — the offer sits as a draft in Seller Hub
    target_sell_price_gbp: strategic price override (e.g. positioning above a
             market's cheapest sellers rather than just at the margin floor).
             Still must clear MIN_NET_MARGIN_PCT — a below-floor override is
             rejected rather than silently listed at a loss.
    """
    if os.path.exists(_PAUSE_FLAG):
        log.warning("Listing PAUSED — PAUSE_LISTINGS flag file present. Delete it to resume.")
        return None

    if count_listings_today() >= DAILY_LISTING_CAP:
        log.warning(f"Daily listing cap ({DAILY_LISTING_CAP}) reached — skipping")
        return None

    keyword = opportunity["keyword"]
    category = opportunity["category"]
    category_id = opportunity["category_id"]
    is_furniture = category in ("furniture", "mattress")
    fee_key = CATEGORIES.get(category, {}).get("fee_key", "everything_else")

    # VeRO check 1: supplier title (before spending a Claude API call)
    supplier_title = supplier_match.get("title", keyword)
    if _vero_blocked(supplier_title):
        log.warning(f"VeRO block (supplier title): '{_first_vero_hit(supplier_title)}' in '{supplier_title[:60]}' — skipping")
        return None

    # Price first, so the listing-content prompt can reference our actual price
    cost = supplier_match["cost_gbp"]
    shipping = supplier_match["shipping_gbp"]
    min_sell_price = _calculate_sell_price(cost, shipping, target_margin=MIN_NET_MARGIN_PCT / 100, fee_key=fee_key)

    if target_sell_price_gbp is not None:
        if target_sell_price_gbp < min_sell_price:
            log.warning(
                f"Requested price £{target_sell_price_gbp} is below the "
                f"{MIN_NET_MARGIN_PCT}% margin floor (£{min_sell_price}) — rejecting, not listing at a loss"
            )
            return None
        sell_price = target_sell_price_gbp
    else:
        sell_price = min_sell_price

    # New accounts: don't list below £12 — low-price items attract SNAD cases
    # that hit defect rate hard before any feedback is built up.
    if sell_price < 12.0 and not is_furniture:
        log.info(f"Sell price £{sell_price} below £12 minimum — skipping '{keyword}'")
        return None

    # Generate optimised title + description, informed by real competitor
    # titles and our actual price for better keyword mining and positioning
    content = _generate_listing_content(
        keyword,
        supplier_title,
        supplier_match.get("description", ""),
        category,
        competitor_titles=opportunity.get("competitor_titles"),
        avg_price_gbp=opportunity.get("avg_price_gbp"),
        our_price_gbp=sell_price,
    )
    title = content["title"]
    description = content["description"]

    # VeRO check 2: Claude-generated title (catches brand mentions introduced by LLM)
    if _vero_blocked(title):
        log.warning(f"VeRO block (generated title): '{_first_vero_hit(title)}' in '{title}' — skipping")
        return None

    # eBay UK policy check: block prohibited/restricted items before they go live
    from research import _is_policy_blocked
    if _is_policy_blocked(title) or _is_policy_blocked(keyword):
        log.warning(f"Policy block: '{title[:70]}' matches restricted term — skipping")
        return None

    _sku_raw = f"{supplier_match['supplier']}-{supplier_match['product_id']}"
    sku = hashlib.md5(_sku_raw.encode()).hexdigest()[:20]
    images = supplier_match.get("images", [])
    if isinstance(images, list):
        images = [str(i) for i in images if i]

    # Resolve a leaf category from the Claude-generated title.
    # Research category IDs are parent categories (Browse searches work fine with them)
    # but eBay's Sell API requires a leaf category — non-leaf triggers error 25005.
    if not is_furniture:
        leaf_fallback = CATEGORIES.get(category, {}).get("leaf_id", category_id)
        category_id = _suggest_ebay_category(title, leaf_fallback)

    log.info(f"Listing: '{title[:60]}...' | cost=£{cost} | sell=£{sell_price} | margin={supplier_match['margin_pct']}%")

    if dry_run:
        print(f"\n[DRY RUN] Would list:")
        print(f"  Title:    {title}")
        print(f"  SKU:      {sku}")
        print(f"  Cost:     £{cost} + £{shipping} shipping")
        print(f"  Sell at:  £{sell_price}")
        print(f"  Margin:   {supplier_match['margin_pct']}%")
        print(f"  Supplier: {supplier_match['supplier']}")
        print(f"  UK warehouse: {supplier_match.get('uk_warehouse', True)}")
        print(f"  Delivery: {supplier_match.get('delivery_days', 'unknown')} days")
        print(f"  Furniture: {is_furniture}")
        return "DRY_RUN"

    # 1. Build required item specifics (aspects) for the category
    aspects = _build_aspects(category, supplier_match, title)
    # "Type" is a required item specific in most eBay UK leaf categories.
    # Furniture sets it explicitly in _build_aspects; for everything else derive
    # it from the keyword so the field is always present.
    if "Type" not in aspects:
        _skip_words = {"and", "or", "for", "with", "in", "of", "set", "sets", "the", "a", "an"}
        type_words = [w.capitalize() for w in keyword.replace("-", " ").split()[:3]
                      if w.lower() not in _skip_words]
        if type_words:
            aspects["Type"] = [" ".join(type_words)]
    if "Brand" not in aspects:
        aspects["Brand"] = ["Unbranded"]
    if "Power Source" not in aspects:
        kw_lower = keyword.lower()
        if any(w in kw_lower for w in ("cordless", "rechargeable", "wireless", "battery")):
            aspects["Power Source"] = ["Battery"]
        elif any(w in kw_lower for w in ("electric", "corded", "plug")):
            aspects["Power Source"] = ["Corded Electric"]
        else:
            aspects["Power Source"] = ["Battery"]
    if "Manufacturer Part Number" not in aspects:
        aspects["Manufacturer Part Number"] = ["Does Not Apply"]

    # 2. Create inventory item
    ok = _get_or_create_inventory_item(sku, title, description, images, category_id, aspects=aspects)
    if not ok:
        return None

    # 2. Create offer
    offer_id = _create_offer(sku, sell_price, category_id, is_furniture=is_furniture,
                              supplier=supplier_match["supplier"],
                              uk_warehouse=supplier_match.get("uk_warehouse", True))
    if not offer_id:
        return None

    if not publish:
        insert_product(
            supplier=supplier_match["supplier"],
            supplier_product_id=supplier_match["product_id"],
            title=title,
            category=category,
            listed_price_gbp=sell_price,
            cost_gbp=supplier_match["total_cost_gbp"],
            margin_pct=supplier_match["margin_pct"],
            ebay_offer_id=offer_id,
            status="draft",
        )
        log.info(f"Draft created: offer ID {offer_id} | '{title[:50]}' — not published")
        return offer_id

    # 3. Publish — on category error, delete the stale offer and retry with the config leaf fallback
    listing_id = _publish_offer(offer_id)
    if listing_id == "25005":
        _delete_offer(offer_id)
        leaf_fallback = CATEGORIES.get(category, {}).get("leaf_id", category_id)
        log.info(f"Retrying with leaf fallback category {leaf_fallback} for '{title[:50]}'")
        offer_id = _create_offer(sku, sell_price, leaf_fallback, is_furniture=is_furniture,
                                  supplier=supplier_match["supplier"],
                                  uk_warehouse=supplier_match.get("uk_warehouse", True))
        if not offer_id:
            return None
        listing_id = _publish_offer(offer_id)
        if not listing_id or listing_id == "25005":
            log.error(f"Category retry also failed for '{title[:50]}' — skipping")
            return None
    if not listing_id:
        return None

    # 4. Record in DB
    insert_product(
        supplier=supplier_match["supplier"],
        supplier_product_id=supplier_match["product_id"],
        title=title,
        category=category,
        listed_price_gbp=sell_price,
        cost_gbp=supplier_match["total_cost_gbp"],
        margin_pct=supplier_match["margin_pct"],
        ebay_listing_id=listing_id,
        ebay_offer_id=offer_id,
    )

    log.info(f"Listed successfully: eBay ID {listing_id} | '{title[:50]}'")
    try:
        from telegram_bot import alert_new_listing
        alert_new_listing(title, sell_price, supplier_match["margin_pct"],
                          supplier_match["supplier"], listing_id)
    except Exception:
        pass
    return listing_id
