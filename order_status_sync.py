"""
Keep the agent's order records in step with what eBay says.

For every order still open in the database, ask eBay for its current state:
  * cancelled / fully refunded  -> status 'cancelled' (profit 0) and a Telegram note so you don't buy it
  * fulfilled (tracking added)   -> status 'dispatched'
Anything else is left alone. Uses only config (token) and the sqlite file, so it also runs against the
old account-1 folder.

    python order_status_sync.py          # run once and print what changed
"""
import logging
import sqlite3
import sys

import requests

from config import EBAY_ORDER_BASE, get_user_token
from db import DB_PATH

log = logging.getLogger("order_status_sync")
OPEN = ("pending", "placed", "failed_placement", "needs_delivery_review")


def _telegram(msg: str):
    try:
        from telegram_bot import _send
        _send(msg)
    except Exception:
        pass


def sync_order_status() -> list[str]:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cols = [r[1] for r in conn.execute("PRAGMA table_info(orders)")]
    rows = conn.execute(f"SELECT * FROM orders WHERE status IN ({','.join('?' * len(OPEN))})", OPEN).fetchall()
    headers = {"Authorization": f"Bearer {get_user_token()}", "X-EBAY-C-MARKETPLACE-ID": "EBAY_GB",
               "Accept": "application/json"}
    changes = []
    for r in rows:
        oid = r["ebay_order_id"]
        try:
            resp = requests.get(f"{EBAY_ORDER_BASE}/order/{oid}", headers=headers, timeout=20)
        except Exception as e:
            log.warning(f"{oid}: lookup failed ({e})")
            continue
        if resp.status_code != 200:
            log.info(f"{oid}: eBay returned {resp.status_code} - left as is")
            continue
        d = resp.json()
        cancel = (d.get("cancelStatus") or {}).get("cancelState", "")
        fulfil = d.get("orderFulfillmentStatus", "")
        refunds = (d.get("paymentSummary") or {}).get("refunds") or []
        refunded = sum(float((x.get("amount") or {}).get("value", 0)) for x in refunds)
        total = float((d.get("pricingSummary", {}).get("total") or {}).get("value", 0) or r["sale_price_gbp"] or 0)
        new = None
        if cancel == "CANCELED" or (total and refunded >= total - 0.01):
            new = "cancelled"
        elif fulfil == "FULFILLED":
            new = "dispatched"
        if new and new != r["status"]:
            sets, vals = ["status=?"], [new]
            if new == "cancelled":
                sets.append("profit_gbp=0")
            conn.execute(f"UPDATE orders SET {', '.join(sets)} WHERE ebay_order_id=?", (*vals, oid))
            conn.commit()
            line = f"{oid}: {r['status']} -> {new}"
            changes.append(line)
            log.info(line)
            if new == "cancelled":
                _telegram(f"ORDER CANCELLED / REFUNDED on eBay: {oid}. Do NOT buy it. "
                          f"If you already bought it from the supplier, cancel or return that order.")
    conn.close()
    return changes


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    out = sync_order_status()
    print(f"{len(out)} order(s) updated" + ("" if not out else ":"))
    for line in out:
        print("  " + line)
