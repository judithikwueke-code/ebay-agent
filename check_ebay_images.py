import sys, os, requests
sys.path.insert(0, '/var/www/ebay-agent-2')
os.chdir('/var/www/ebay-agent-2')
from dotenv import load_dotenv; load_dotenv()
import sqlite3
from db import DB_PATH
from config import get_user_token

# Check eBay inventory items for our recent listings
conn = sqlite3.connect(DB_PATH)
rows = conn.execute("""
    SELECT ebay_listing_id, ebay_offer_id, supplier_product_id, title, cost_gbp
    FROM products
    WHERE status='active'
    ORDER BY created_at DESC LIMIT 10
""").fetchall()
conn.close()

headers = {
    'Authorization': 'Bearer ' + get_user_token(),
    'Content-Type': 'application/json',
    'Accept-Language': 'en-GB',
}

print("Checking eBay inventory for images...")
for ebay_id, offer_id, asin, title, cost in rows:
    url = f'https://api.ebay.com/sell/inventory/v1/inventory_item'
    # Use offer to get SKU
    if offer_id:
        r = requests.get(f'https://api.ebay.com/sell/inventory/v1/offer/{offer_id}', headers=headers)
        if r.status_code == 200:
            data = r.json()
            sku = data.get('sku', '')
            # Now get inventory item
            r2 = requests.get(f'https://api.ebay.com/sell/inventory/v1/inventory_item/{sku}', headers=headers)
            if r2.status_code == 200:
                item = r2.json()
                imgs = item.get('product', {}).get('imageUrls', [])
                print(f"  {ebay_id} | {asin} | imgs={len(imgs)} | {title[:40]}")
                if not imgs:
                    print(f"    -> NO IMAGES (SKU: {sku})")
                for img in imgs[:2]:
                    print(f"    {img[:80]}")
            else:
                print(f"  {ebay_id} | inventory item fetch failed {r2.status_code}")
        else:
            print(f"  {ebay_id} | offer fetch failed {r.status_code}")
