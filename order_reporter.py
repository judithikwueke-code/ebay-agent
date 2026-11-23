"""Order & cancellation reporter (account 2). It ONLY reports - it never buys, ships, edits or cancels anything.

Every run (cron, every 3 minutes) it asks eBay which orders changed in the last 3 days and sends Telegram messages for:
  * NEW ORDER            - once per order
  * CANCELLATION         - buyer requested a cancel / order cancelled / fully refunded (once per state)

Each customer detail goes on its own line inside a tap-to-copy block (Telegram <code>): name, phone, address, postcode.
If the delivery address is an eBay Collection Point (an "eCP:XXXXXXXX" code in the address) the message opens with a
bold ECP banner and the ECP number gets its own copy block.

    python order_reporter.py              # normal run (cron)
    python order_reporter.py --seed       # mark everything currently on eBay as already reported (first install)
    python order_reporter.py --replay N   # re-send the N most recent orders (to check the layout)
    python order_reporter.py --dry        # print instead of sending

State: order_reporter_state.json. Switch off the old duplicate alerts with LEGACY_ORDER_ALERTS=0 in .env.
"""
import fcntl
import html
import json
import logging
import os
import re
import sqlite3
import sys
from datetime import datetime, timedelta, timezone

import requests

HERE = os.path.dirname(os.path.abspath(__file__))
os.chdir(HERE)
from dotenv import load_dotenv
load_dotenv(os.path.join(HERE, ".env"))
from config import EBAY_ORDER_BASE, get_user_token          # noqa: E402
from db import DB_PATH                                        # noqa: E402

log = logging.getLogger("order_reporter")
STATE_FILE = os.path.join(HERE, "order_reporter_state.json")
LOOKBACK_DAYS = 3
ECP_RE = re.compile(r"\be\s?CP\s*[:\-#]?\s*([A-Z0-9]{5,})", re.I)
TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
ORDER_TOKEN = os.getenv("ORDER_BOT_TOKEN")      # dedicated order bot (@Ebay_Agent_2_bot); main bot is the fallback
CHAT = os.getenv("TELEGRAM_CHAT_ID")
LABEL = os.getenv("ACCOUNT_LABEL", "")


def esc(x) -> str:
    return html.escape(str(x or "").strip(), quote=False)


def copy(x) -> str:
    """One tap-to-copy value. Empty values say so instead of an empty block."""
    x = " ".join(str(x or "").split())
    return f"<code>{esc(x)}</code>" if x else "<i>not given</i>"


def telegram(text: str, dry=False) -> bool:
    if dry:
        print(re.sub(r"<[^>]+>", "", html.unescape(text)) + "\n" + "=" * 50)
        return True
    for tok in [t for t in (ORDER_TOKEN, TOKEN) if t]:      # dedicated bot first, main bot as the safety net
        for attempt in range(2):
            try:
                r = requests.post(f"https://api.telegram.org/bot{tok}/sendMessage", timeout=15, json={
                    "chat_id": CHAT, "text": text[:4000], "parse_mode": "HTML", "disable_web_page_preview": True})
                if r.ok:
                    return True
                log.warning("telegram rejected (%s bot): %s %s", "order" if tok == ORDER_TOKEN else "main",
                            r.status_code, r.text[:120])
                break                   # a rejection (e.g. chat not started) will not fix itself by retrying
            except Exception as e:
                log.warning("telegram failed: %s", e)
    return False


def headers():
    return {"Authorization": f"Bearer {get_user_token()}", "X-EBAY-C-MARKETPLACE-ID": "EBAY_GB", "Accept": "application/json"}


def fetch_orders(days=LOOKBACK_DAYS):
    since = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    out, offset = [], 0
    while True:
        r = requests.get(f"{EBAY_ORDER_BASE}/order", headers=headers(), timeout=25,
                         params={"filter": f"lastmodifieddate:[{since}]", "limit": 100, "offset": offset})
        r.raise_for_status()
        d = r.json()
        out += d.get("orders", [])
        offset += 100
        if offset >= int(d.get("total", 0) or 0) or not d.get("orders"):
            return out


def address_of(order: dict) -> dict:
    ship = (order.get("fulfillmentStartInstructions") or [{}])[0].get("shippingStep", {}).get("shipTo", {})
    c = ship.get("contactAddress") or ship
    phone = (ship.get("primaryPhone") or {}).get("phoneNumber", "") or ship.get("phone", "")
    lines = [c.get("addressLine1", ""), c.get("addressLine2", "")]
    town = ", ".join(x for x in (c.get("city", ""), c.get("stateOrProvince", "")) if x)
    full = ", ".join(x for x in lines + [town] if x)
    blob = " ".join([ship.get("fullName", "")] + lines)
    m = ECP_RE.search(blob)
    return {"name": ship.get("fullName", ""), "phone": phone, "address": full, "postcode": c.get("postalCode", ""),
            "ecp": ("eCP:" + m.group(1).upper()) if m else ""}


def cancel_state(order: dict) -> str:
    """'' | 'REQUESTED' (buyer asked) | 'CANCELLED' (cancelled or fully refunded)."""
    cs = order.get("cancelStatus") or {}
    state = cs.get("cancelState", "")
    refunds = (order.get("paymentSummary") or {}).get("refunds") or []
    refunded = sum(float((x.get("amount") or {}).get("value", 0) or 0) for x in refunds)
    total = float(((order.get("pricingSummary") or {}).get("total") or {}).get("value", 0) or 0)
    if state == "CANCELED" or (total and refunded >= total - 0.01):
        return "CANCELLED"
    if any(x.get("cancelRequestState") == "REQUESTED" for x in (cs.get("cancelRequests") or [])) or state == "IN_PROGRESS":
        return "REQUESTED"
    return ""


def product_info(listing_id: str):
    """(supplier, source link, cost incl. VAT per unit) from our database, or None for a listing we did not create."""
    try:
        c = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
        c.row_factory = sqlite3.Row
        p = c.execute("SELECT * FROM products WHERE ebay_listing_id=?", (listing_id,)).fetchone()
        c.close()
    except Exception:
        return None
    if not p:
        return None
    sup, pid = p["supplier"], p["supplier_product_id"]
    link = f"https://www.amazon.co.uk/dp/{pid}?th=1&psc=1" if sup == "amazon" else ""
    return {"supplier": sup, "link": link, "profit": None}


def build_order_msg(order: dict) -> str:
    a = address_of(order)
    items = order.get("lineItems", [])
    total = ((order.get("pricingSummary") or {}).get("total") or {}).get("value", "?")
    oid = order.get("orderId", "")
    out = []
    if a["ecp"]:
        out += ["🚨🚨 <b>ECP — EBAY COLLECTION POINT ORDER</b> 🚨🚨", f"<b>ECP NUMBER:</b> {copy(a['ecp'])}",
                "<b>⚠ Put the FULL address, including the ECP number, in the supplier's address lines. "
                "Without it the parcel can't be collected.</b>", ""]
    out.append(f"<b>NEW ORDER</b>{(' [' + esc(LABEL) + ']') if LABEL else ''}")
    for it in items:
        lid = it.get("legacyItemId") or ""
        qty = it.get("quantity", 1)
        cost = (it.get("lineItemCost") or {}).get("value", "?")
        out.append(f"• {esc(str(it.get('title', ''))[:90])}")
        out.append(f"  Qty <b>{esc(qty)}</b> · £{esc(cost)} · listing {esc(lid)}")
        info = product_info(lid)
        if info and info["link"]:
            out.append(f"  Buy: {esc(info['link'])}")
        elif not info:
            out.append("  ⚠ not one of the agent's listings — handle by hand")
    out.append(f"Order total £{esc(total)} · order <code>{esc(oid)}</code>")
    out += ["", "<b>NAME</b>", copy(a["name"]),
            "<b>PHONE</b>", copy(a["phone"]),
            "<b>ADDRESS</b>", copy(a["address"]),
            "<b>POSTCODE</b>", copy(a["postcode"])]
    return "\n".join(out)


def build_cancel_msg(order: dict, state: str) -> str:
    a = address_of(order)
    oid = order.get("orderId", "")
    head = ("⛔ <b>CANCELLATION REQUESTED BY BUYER</b>\nDo NOT buy it yet — answer the request in eBay."
            if state == "REQUESTED" else
            "❌ <b>ORDER CANCELLED / REFUNDED</b>\nDo NOT buy it. If you already bought it, cancel or return it with the supplier.")
    out = []
    if a["ecp"]:
        out += ["🚨 <b>ECP ORDER</b> 🚨", f"<b>ECP NUMBER:</b> {copy(a['ecp'])}", ""]
    out.append(head)
    for it in order.get("lineItems", []):
        out.append(f"• {esc(str(it.get('title', ''))[:90])} (qty {esc(it.get('quantity', 1))})")
    out += [f"Order <code>{esc(oid)}</code>", "",
            "<b>NAME</b>", copy(a["name"]), "<b>PHONE</b>", copy(a["phone"]),
            "<b>ADDRESS</b>", copy(a["address"]), "<b>POSTCODE</b>", copy(a["postcode"])]
    return "\n".join(out)


def load_state():
    try:
        return json.load(open(STATE_FILE))
    except Exception:
        return {"orders": {}, "fails": 0}


def save_state(s):
    tmp = STATE_FILE + ".tmp"
    json.dump(s, open(tmp, "w"), indent=1)
    os.replace(tmp, STATE_FILE)


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    args = sys.argv[1:]
    dry = "--dry" in args
    lock = open("/tmp/order_reporter.lock", "w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        return
    st = load_state()
    try:
        orders = fetch_orders()
        st["fails"] = 0
    except Exception as e:
        st["fails"] = st.get("fails", 0) + 1
        log.error("could not read orders: %s", e)
        if st["fails"] == 5:        # ~15 minutes of failures: tell the owner once per outage
            telegram("⚠ The order reporter can't read eBay orders (" + esc(str(e)[:100]) + "). Check eBay orders by hand until this clears.", dry)
        save_state(st)
        return
    orders.sort(key=lambda o: o.get("creationDate", ""))
    if "--replay" in args:
        n = int(args[args.index("--replay") + 1])
        for o in orders[-n:]:
            telegram(build_order_msg(o), dry)
            cs = cancel_state(o)
            if cs:
                telegram(build_cancel_msg(o, cs), dry)
        return
    seed = "--seed" in args
    sent = 0
    for o in orders:
        oid = o.get("orderId")
        if not oid:
            continue
        rec = st["orders"].setdefault(oid, {"new": False, "cancel": ""})
        cs = cancel_state(o)
        if not rec["new"]:
            if seed or telegram(build_order_msg(o), dry):
                rec["new"] = True
                sent += 0 if seed else 1
        if cs and rec["cancel"] != cs and (seed or rec["new"]):
            if seed or telegram(build_cancel_msg(o, cs), dry):
                rec["cancel"] = cs
                sent += 0 if seed else 1
    cutoff = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
    st["orders"] = {k: v for k, v in st["orders"].items() if k in {o.get("orderId") for o in orders} or v.get("seen", cutoff) >= cutoff}
    if not dry:
        save_state(st)
    log.info("%d order(s) checked, %d message(s) sent%s", len(orders), sent, " (seed)" if seed else "")


if __name__ == "__main__":
    main()
