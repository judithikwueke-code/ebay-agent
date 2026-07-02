"""
Polls eBay UK for new orders, routes them to the correct supplier,
and syncs tracking numbers back to eBay.
"""

import logging
import requests
from datetime import datetime, timezone, timedelta
from config import (
    EBAY_ORDER_BASE, EBAY_MARKETPLACE, get_user_token,
)
from db import (
    get_product_by_listing, insert_order, update_order,
    get_pending_orders, orders_without_tracking,
)
from supplier import place_supplier_order, get_tracking

log = logging.getLogger(__name__)

def _order_headers() -> dict:
    return {
        "Authorization": f"Bearer {get_user_token()}",
        "X-EBAY-C-MARKETPLACE-ID": "EBAY_GB",
        "Content-Type": "application/json",
    }


# ---------------------------------------------------------------------------
# Fetch new eBay orders
# ---------------------------------------------------------------------------

def _fetch_new_ebay_orders(since_minutes: int = 20) -> list[dict]:
    """Fetch orders created in the last `since_minutes` minutes."""
    since = (datetime.now(timezone.utc) - timedelta(minutes=since_minutes)).strftime(
        "%Y-%m-%dT%H:%M:%S.000Z"
    )
    url = f"{EBAY_ORDER_BASE}/order"
    params = {
        "filter": f"creationdate:[{since}]",
        "limit": 50,
    }
    try:
        resp = requests.get(url, headers=_order_headers(), params=params, timeout=15)
        resp.raise_for_status()
        return resp.json().get("orders", [])
    except Exception as e:
        log.error(f"Failed to fetch eBay orders: {e}")
        return []


def _parse_address(shipping_address: dict) -> dict:
    """Normalise eBay shipping address to a flat dict for supplier APIs.
    eBay Fulfillment API nests address fields under contactAddress; the older
    Trading API returns them flat. Handle both."""
    contact = shipping_address.get("contactAddress") or shipping_address
    phone_obj = shipping_address.get("primaryPhone") or {}
    return {
        "name": shipping_address.get("fullName", ""),
        "line1": contact.get("addressLine1", ""),
        "line2": contact.get("addressLine2", ""),
        "city": contact.get("city", ""),
        "county": contact.get("stateOrProvince", ""),
        "postcode": contact.get("postalCode", ""),
        "country": contact.get("countryCode", "GB"),
        "phone": phone_obj.get("phoneNumber", "") or shipping_address.get("phone", ""),
    }


# ---------------------------------------------------------------------------
# Mark eBay order as dispatched with tracking
# ---------------------------------------------------------------------------

def _mark_dispatched_on_ebay(ebay_order_id: str, tracking_number: str, carrier: str = "Royal Mail") -> bool:
    url = f"{EBAY_ORDER_BASE}/order/{ebay_order_id}/shipping_fulfillment"
    payload = {
        "lineItems": [],  # eBay auto-links all line items when empty
        "shippedDate": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z"),
        "shippingCarrierCode": carrier,
        "trackingNumber": tracking_number,
    }
    try:
        resp = requests.post(url, headers=_order_headers(), json=payload, timeout=15)
        if resp.status_code in (200, 201):
            log.info(f"eBay order {ebay_order_id} marked dispatched with tracking {tracking_number}")
            return True
        log.error(f"Mark dispatched failed [{resp.status_code}]: {resp.text[:200]}")
        return False
    except Exception as e:
        log.error(f"Mark dispatched exception: {e}")
        return False


# ---------------------------------------------------------------------------
# Process new orders
# ---------------------------------------------------------------------------

def process_new_orders():
    """
    Main polling task — called every ORDER_POLL_MINUTES.
    1. Fetch recent eBay orders
    2. For each new order: look up product → place with supplier → record in DB
    """
    orders = _fetch_new_ebay_orders()
    if not orders:
        log.debug("No new eBay orders")
        return

    log.info(f"Found {len(orders)} new order(s)")

    for order in orders:
        ebay_order_id = order.get("orderId", "")
        if not ebay_order_id:
            continue

        line_items = order.get("lineItems", [])
        if not line_items:
            continue

        shipping_addr = order.get("fulfillmentStartInstructions", [{}])[0].get(
            "shippingStep", {}
        ).get("shipTo", {})
        buyer_address = _parse_address(shipping_addr)
        buyer_name = buyer_address["name"]

        for item in line_items:
            # eBay Fulfillment API uses legacyItemId (the public listing number),
            # not listingId. Fall back to sku-based lookup if listing not found.
            listing_id = item.get("legacyItemId") or item.get("listingId", "")
            sale_price = float(item.get("lineItemCost", {}).get("value", 0))

            product = get_product_by_listing(listing_id)
            if not product:
                log.warning(f"Order {ebay_order_id}: listing {listing_id} not in DB — manual review needed")
                continue

            supplier = product["supplier"]
            supplier_product_id = product["supplier_product_id"]
            cost = product["cost_gbp"]
            is_furniture = product["category"] == "furniture"

            # Record order in DB first
            insert_order(
                ebay_order_id=ebay_order_id,
                product_id=product["id"],
                supplier=supplier,
                sale_price_gbp=sale_price,
                cost_gbp=cost,
                buyer_name=buyer_name,
                buyer_address=str(buyer_address),
                is_furniture=is_furniture,
            )

            if is_furniture:
                log.info(
                    f"FURNITURE ORDER {ebay_order_id} — supplier={supplier} — "
                    "flagged for manual delivery slot review"
                )
                # Still place with supplier, but flag it
                update_order(ebay_order_id, status="needs_delivery_review")

            # Telegram alert for every new order
            try:
                from telegram_bot import alert_new_order
                profit = sale_price - cost - (sale_price * 0.13)
                alert_new_order(product["title"], sale_price, profit, buyer_name)
            except Exception:
                pass

            # Place order with supplier
            result = place_supplier_order(supplier, supplier_product_id, buyer_address)
            if result:
                update_order(
                    ebay_order_id,
                    supplier_order_id=result["supplier_order_id"],
                    status="placed",
                )
                log.info(f"Order {ebay_order_id} placed with {supplier}: {result['supplier_order_id']}")
            else:
                update_order(ebay_order_id, status="failed_placement")
                log.error(f"Order {ebay_order_id}: supplier order placement failed — manual action required")


# ---------------------------------------------------------------------------
# Sync tracking numbers
# ---------------------------------------------------------------------------

def sync_tracking():
    """
    Called every SUPPLIER_TRACKING_HOURS.
    For all placed orders without tracking: poll supplier → update eBay.
    """
    pending = orders_without_tracking()
    if not pending:
        log.debug("No orders awaiting tracking")
        return

    log.info(f"Checking tracking for {len(pending)} order(s)")

    for order in pending:
        supplier = order["supplier"]
        supplier_order_id = order.get("supplier_order_id")
        ebay_order_id = order["ebay_order_id"]

        if not supplier_order_id:
            continue

        tracking = get_tracking(supplier, supplier_order_id)
        if not tracking:
            log.debug(f"No tracking yet for {ebay_order_id} / {supplier_order_id}")
            continue

        # Update eBay
        carrier = _guess_carrier(tracking)
        dispatched = _mark_dispatched_on_ebay(ebay_order_id, tracking, carrier)

        if dispatched:
            update_order(
                ebay_order_id,
                tracking_number=tracking,
                status="dispatched",
                dispatched_at=datetime.now(timezone.utc).isoformat(),
            )
        else:
            log.warning(f"Got tracking {tracking} but failed to update eBay for order {ebay_order_id}")


def _guess_carrier(tracking: str) -> str:
    """Heuristic carrier detection from tracking number format."""
    tracking = tracking.upper().strip()
    if tracking.startswith(("JD", "JJD")):
        return "DPD"
    if tracking.startswith(("TT", "RR", "CP")) and tracking.endswith("GB"):
        return "Royal Mail"
    if len(tracking) == 18 and tracking.isdigit():
        return "Hermes"
    if tracking.startswith("1Z"):
        return "UPS"
    if tracking.startswith(("DHL", "JV")):
        return "DHL"
    return "Royal Mail"  # safe default for UK
