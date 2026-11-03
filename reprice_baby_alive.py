import sys, os, requests
sys.path.insert(0, '/var/www/ebay-agent-2')
os.chdir('/var/www/ebay-agent-2')
from dotenv import load_dotenv; load_dotenv()
from config import get_user_token
import sqlite3
from db import DB_PATH

# cost=23.30, fees=10.9%+0.30, target profit=£5
# sell = (cost + 0.30 + 5) / (1 - 0.109) = 28.60 / 0.891 = £32.10
NEW_PRICE = 32.10
EBAY_LISTING_ID = '366777169779'

conn = sqlite3.connect(DB_PATH)
row = conn.execute("SELECT ebay_offer_id FROM products WHERE ebay_listing_id=?", (EBAY_LISTING_ID,)).fetchone()
conn.close()
offer_id = row[0]

headers = {
    'Authorization': 'Bearer ' + get_user_token(),
    'Content-Type': 'application/json',
    'Content-Language': 'en-GB',
}

# Get current offer
r = requests.get(f'https://api.ebay.com/sell/inventory/v1/offer/{offer_id}', headers=headers)
offer = r.json()

# Update price
offer['pricingSummary']['price']['value'] = str(NEW_PRICE)
offer['pricingSummary']['price']['currency'] = 'GBP'

r2 = requests.put(
    f'https://api.ebay.com/sell/inventory/v1/offer/{offer_id}',
    headers=headers,
    json=offer
)
if r2.status_code in (200, 204):
    conn = sqlite3.connect(DB_PATH)
    conn.execute("UPDATE products SET listed_price_gbp=? WHERE ebay_listing_id=?", (NEW_PRICE, EBAY_LISTING_ID))
    conn.commit()
    conn.close()
    print(f"Repriced to GBP{NEW_PRICE} (profit ~GBP5 after GBP23.30 cost + fees)")
else:
    print(f"Failed {r2.status_code}: {r2.text[:200]}")
