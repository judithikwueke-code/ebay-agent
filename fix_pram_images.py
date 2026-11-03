import sys, os, requests, json
sys.path.insert(0, '/var/www/ebay-agent-2')
os.chdir('/var/www/ebay-agent-2')
from dotenv import load_dotenv; load_dotenv()
from config import get_user_token

headers = {
    'Authorization': 'Bearer ' + get_user_token(),
    'Content-Type': 'application/json',
    'Content-Language': 'en-GB',
    'Accept-Language': 'en-GB',
}

# offer_id -> new images
fixes = [
    # GLOKID (offer 336909901011 is Baby Alive, need to find correct offer IDs)
    # Let's look up by listing ID
]

# Get SKUs from offer IDs for our broken listings
broken = {
    '366777172623': ('B0D3PTLYBP', 'https://m.media-amazon.com/images/I/61NvIcPeOtL.jpg'),  # GLOKID
    '366777173706': ('B0GF2T3477', 'https://m.media-amazon.com/images/I/610-GxPMxHL.jpg'),  # Bebeconfort
}

import sqlite3
from db import DB_PATH
conn = sqlite3.connect(DB_PATH)

for ebay_listing_id, (asin, new_img) in broken.items():
    row = conn.execute(
        "SELECT ebay_offer_id FROM products WHERE ebay_listing_id=?", (ebay_listing_id,)
    ).fetchone()
    if not row:
        print("No offer found for", ebay_listing_id)
        continue
    offer_id = row[0]
    
    # Get offer to find SKU
    r = requests.get(f'https://api.ebay.com/sell/inventory/v1/offer/{offer_id}', headers=headers)
    if r.status_code != 200:
        print(f"Offer fetch failed {r.status_code} for {offer_id}")
        continue
    sku = r.json().get('sku', '')
    print(f"Fixing {ebay_listing_id} | SKU: {sku} | new image: {new_img[:60]}")
    
    # Get current inventory item
    r2 = requests.get(f'https://api.ebay.com/sell/inventory/v1/inventory_item/{sku}', headers=headers)
    if r2.status_code != 200:
        print(f"  Inventory item fetch failed {r2.status_code}")
        continue
    item = r2.json()
    
    # Update image
    item.setdefault('product', {})['imageUrls'] = [new_img]
    
    # PUT updated item back
    r3 = requests.put(
        f'https://api.ebay.com/sell/inventory/v1/inventory_item/{sku}',
        headers=headers,
        json=item
    )
    if r3.status_code in (200, 204):
        print(f"  Image updated successfully")
    else:
        print(f"  Update failed {r3.status_code}: {r3.text[:200]}")

conn.close()
