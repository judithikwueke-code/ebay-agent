"""
One-off: upload a tracking number to eBay and mark the order dispatched.
Usage: python upload_tracking.py <ebay_order_id> <tracking_number> [carrier]
Example: python upload_tracking.py 10-14788-84978 JD123456789GB "Royal Mail"
"""
import sys
from orders import _mark_dispatched_on_ebay
from db import update_order

ebay_order_id = sys.argv[1]
tracking = sys.argv[2]
carrier = sys.argv[3] if len(sys.argv) > 3 else "Royal Mail"

ok = _mark_dispatched_on_ebay(ebay_order_id, tracking, carrier)
if ok:
    update_order(ebay_order_id, tracking_number=tracking, status="dispatched")
    print(f"Done — eBay order {ebay_order_id} marked dispatched, tracking={tracking}")
else:
    print("FAILED — check logs above")
