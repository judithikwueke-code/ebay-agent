"""
Publish one hand-specified item (not sourced by the automatic pipeline).

Currently holds the Sports Direct Jack Wills Lancaster 4-piece bedroom set.
    python list_manual.py --check      # resolve category + required item specifics, create nothing
    python list_manual.py              # create policy (if needed), inventory item, offer, publish
"""
import hashlib
import json
import logging
import sys

import requests

from config import (
    EBAY_PAYMENT_POLICY_ID, EBAY_RETURN_POLICY_ID, EBAY_MERCHANT_LOCATION_KEY,
    EBAY_FURNITURE_FULFILLMENT_POLICY_ID, get_app_token,
)
from db import init_db, insert_product, is_product_already_listed
from lister import _sell_headers, _suggest_ebay_category, _publish_offer

log = logging.getLogger("list_manual")

PRODUCT_PATH = "jack-wills-lancaster-bedroom-4-piece-set-702263#colcode=70226302"
PRODUCT_URL = "https://www.sportsdirect.com/" + PRODUCT_PATH
SELL_PRICE = 280.50
SHELF_COST = 224.99          # Sports Direct price, VAT-inclusive
DELIVERY_COST = 49.99        # 2-person delivery
HANDLING_DAYS = 14           # Sports Direct: delivered within 3-14 working days
POLICY_NAME = "Furniture Sports Direct 14 day"

TITLE = "Lancaster 4 Piece Grey Bedroom Furniture Set 3 Door Wardrobe Chest of Drawers"
IMAGES = [f"https://cdn.media.amplience.net/i/frasersdev/70226302_o{suffix}?fmt=jpg&w=1200"
          for suffix in ("", "_a1", "_a2", "_a3", "_a4", "_a5", "_a6", "_a7")]

DESCRIPTION = """
<h2>Lancaster 4 Piece Grey Bedroom Furniture Set - Wardrobe, Chest of Drawers &amp; 2 Bedside Tables</h2>
<p>Complete your bedroom in one go with this stylish <b>Lancaster grey bedroom furniture set</b>. A classic, modern finish with
metal handles makes it a stunning centrepiece for any bedroom, and the matching pieces give you plenty of storage.</p>
<h3>What's in the 4 piece bedroom set</h3>
<ul>
<li><b>3 door wardrobe</b> - hanging rail and top shelf behind the left and middle doors, rail with middle shelf behind the right door</li>
<li><b>4 drawer chest of drawers</b> - spacious storage for clothes and bedding</li>
<li><b>2 x bedside tables</b> - handy storage either side of the bed</li>
</ul>
<h3>Size</h3>
<ul>
<li>3 door wardrobe: H180.5 x W112 x D52cm</li>
<li>4 drawer chest: H95.5 x W79 x D39.5cm</li>
<li>Bedside table: H48 x W45 x D40cm</li>
</ul>
<h3>Key features</h3>
<ul>
<li>Colour: grey</li>
<li>Material: wood, with metal handles</li>
<li>Matching 4 piece set - wardrobe, chest and 2 bedsides</li>
<li>Easy to assemble at home - flat pack</li>
<li>Care: wipe clean with a dry cloth only</li>
</ul>
<h3>Delivery &amp; returns</h3>
<p>Free delivery to UK mainland by a 2-person delivery team, arriving within 14 working days of purchase.
Large item - please make sure someone is available to receive it. We cannot deliver to the Channel Islands.
Returns accepted - see our returns policy.</p>
""".strip()


def _app_headers():
    return {"Authorization": f"Bearer {get_app_token()}", "X-EBAY-C-MARKETPLACE-ID": "EBAY_GB", "Accept": "application/json"}


def _required_aspects(category_id: str) -> list[dict]:
    r = requests.get(
        f"https://api.ebay.com/commerce/taxonomy/v1/category_tree/3/get_item_aspects_for_category",
        headers=_app_headers(), params={"category_id": category_id}, timeout=20)
    r.raise_for_status()
    return r.json().get("aspects", [])


def _fill_aspects(category_id: str) -> tuple[dict, list[str]]:
    known = {
        "brand": "Jack Wills", "colour": "Grey", "color": "Grey", "material": "Wood",
        "room": "Bedroom", "type": "Bedroom Furniture Set", "assembly required": "Yes",
        "manufacturer part number": "702263", "mpn": "702263", "style": "Modern",
        "number of pieces": "4", "features": "Metal Handles", "item height": "180.5 cm",
        "country/region of manufacture": "Unknown", "mattress included": "No",
    }
    aspects, missing = {}, []
    for a in _required_aspects(category_id):
        if not a.get("aspectConstraint", {}).get("aspectRequired"):
            continue
        name = a["localizedAspectName"]
        value = known.get(name.lower())
        allowed = [v["localizedValue"] for v in a.get("aspectValues", [])]
        if value and allowed and a.get("aspectConstraint", {}).get("aspectMode") == "SELECTION_ONLY" and value not in allowed:
            match = next((x for x in allowed if x.lower() == value.lower()), None)
            value = match
        if value:
            aspects[name] = [value]
        else:
            missing.append(name)
    if "Set Includes" in missing:
        aspects["Set Includes"] = ["Wardrobe", "Chest of Drawers", "Bedside Table"]
        missing.remove("Set Includes")
    if "Number of Items in Set" in missing:
        aspects["Number of Items in Set"] = ["4"]
        missing.remove("Number of Items in Set")
    for extra in ("Brand", "Colour", "Material", "Type"):
        aspects.setdefault(extra, [known[extra.lower()]])
    return aspects, missing


def _ensure_policy() -> str:
    base = "https://api.ebay.com/sell/account/v1/fulfillment_policy"
    existing = requests.get(base, headers=_sell_headers(), params={"marketplace_id": "EBAY_GB"}, timeout=20).json()
    for p in existing.get("fulfillmentPolicies", []):
        if p["name"] == POLICY_NAME:
            return p["fulfillmentPolicyId"]
    src = next(p for p in existing["fulfillmentPolicies"] if p["fulfillmentPolicyId"] == EBAY_FURNITURE_FULFILLMENT_POLICY_ID)
    for k in ("fulfillmentPolicyId",):
        src.pop(k, None)
    src["name"] = POLICY_NAME
    # Longest handling time this account is allowed (limits vary by account); start at the source's worst case.
    for days in range(HANDLING_DAYS, 2, -1):
        src["handlingTime"] = {"value": days, "unit": "DAY"}
        r = requests.post(base, headers=_sell_headers(), data=json.dumps(src), timeout=20)
        if r.status_code < 300:
            print(f"Policy created with {days}-day handling")
            break
        if "INVALID_HANDLING_TIME" not in r.text:
            raise SystemExit(f"Fulfillment policy create failed [{r.status_code}]: {r.text[:700]}")
    else:
        raise SystemExit("No valid handling time found")
    return r.json()["fulfillmentPolicyId"]


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s - %(message)s")
    check = "--check" in sys.argv
    init_db()
    if is_product_already_listed("sportsdirect", PRODUCT_PATH):
        print("Already listed - nothing to do")
        sys.exit(0)

    category_id = _suggest_ebay_category("Bedroom Furniture Set wardrobe chest of drawers bedside tables", "20736")
    aspects, missing = _fill_aspects(category_id)
    print("CATEGORY", category_id)
    print("ASPECTS", json.dumps(aspects))
    print("MISSING REQUIRED", missing)
    print("TITLE", len(TITLE), TITLE)
    if check:
        sys.exit(0)
    if missing:
        print("Required item specifics unresolved - stopping")
        sys.exit(1)

    sku = hashlib.md5(f"sportsdirect-{PRODUCT_PATH}".encode()).hexdigest()[:20]
    policy_id = _ensure_policy()
    print("FULFILLMENT POLICY", policy_id)

    inv = {
        "availability": {"shipToLocationAvailability": {"quantity": 1}},
        "condition": "NEW",
        "product": {"title": TITLE, "description": DESCRIPTION, "imageUrls": IMAGES, "aspects": aspects},
    }
    r = requests.put(f"https://api.ebay.com/sell/inventory/v1/inventory_item/{sku}", headers=_sell_headers(), json=inv, timeout=30)
    if r.status_code not in (200, 201, 204):
        print("Inventory item failed", r.status_code, r.text[:600])
        sys.exit(1)

    offer = {
        "sku": sku, "marketplaceId": "EBAY_GB", "format": "FIXED_PRICE", "availableQuantity": 1,
        "categoryId": category_id, "listingDescription": DESCRIPTION,
        "listingPolicies": {"fulfillmentPolicyId": policy_id, "paymentPolicyId": EBAY_PAYMENT_POLICY_ID,
                            "returnPolicyId": EBAY_RETURN_POLICY_ID},
        "pricingSummary": {"price": {"value": f"{SELL_PRICE:.2f}", "currency": "GBP"}},
        "merchantLocationKey": EBAY_MERCHANT_LOCATION_KEY,
    }
    r = requests.post("https://api.ebay.com/sell/inventory/v1/offer", headers=_sell_headers(), json=offer, timeout=30)
    if r.status_code not in (200, 201):
        print("Offer failed", r.status_code, r.text[:600])
        sys.exit(1)
    offer_id = r.json()["offerId"]
    listing_id = _publish_offer(offer_id)
    if not listing_id or listing_id == "25005":
        print("Publish failed - offer left as draft:", offer_id)
        sys.exit(1)

    cost_ex_vat = round((SHELF_COST + DELIVERY_COST) / 1.2, 2)
    insert_product(
        supplier="sportsdirect", supplier_product_id=PRODUCT_PATH, title=TITLE, category="furniture",
        listed_price_gbp=SELL_PRICE, cost_gbp=cost_ex_vat,
        margin_pct=round((SELL_PRICE - SHELF_COST - DELIVERY_COST) / SELL_PRICE * 100, 2),
        ebay_listing_id=listing_id, ebay_offer_id=offer_id,
    )
    print("LISTED", listing_id)
