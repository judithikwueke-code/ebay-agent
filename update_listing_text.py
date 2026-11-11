"""Change title/description of one own-stock listing. Usage: update_listing_text.py <sku_id> <spec.json>
spec.json: {"title": "...", "description_html": "..."}"""
import hashlib
import json
import sys

import requests
from config import EBAY_SELL_BASE
from lister import _sell_headers

sku_id, spec = sys.argv[1], json.load(open(sys.argv[2], encoding="utf-8"))
sku = hashlib.md5(f"own-{sku_id}".encode()).hexdigest()[:20]
url = f"{EBAY_SELL_BASE}/inventory_item/{sku}"
item = requests.get(url, headers=_sell_headers(), timeout=20).json()
for k in ("sku", "locale"):
    item.pop(k, None)
print("before:", item["product"]["title"])
item["product"]["title"] = spec["title"][:80]
item["product"]["description"] = spec["description_html"]
r = requests.put(url, headers=_sell_headers(), json=item, timeout=30)
print("PUT", r.status_code, r.text[:200])
