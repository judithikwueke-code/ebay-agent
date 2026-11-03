import sys, os, logging
sys.path.insert(0, '/var/www/ebay-agent-2')
os.chdir('/var/www/ebay-agent-2')
from dotenv import load_dotenv; load_dotenv()
logging.basicConfig(level=logging.INFO, format='%(levelname)s - %(message)s')

from supplier import find_best_supplier
from lister import create_listing
from db import is_product_already_listed
import sqlite3
from db import DB_PATH

ASIN = 'B0BDHM2XHM'
KEYWORD = 'baby alive doll feeding'
EBAY_PRICE = 42.99  # slightly under market avg of 45.38 to be competitive

# Check not already listed
conn = sqlite3.connect(DB_PATH)
existing = conn.execute(
    "SELECT ebay_item_id FROM listings WHERE supplier_sku LIKE ? AND status='active'",
    ('%' + ASIN + '%',)
).fetchone()
conn.close()

if existing:
    print("Already listed:", existing[0])
    sys.exit(0)

print("Finding best supplier for ASIN", ASIN)
supplier = find_best_supplier(KEYWORD, max_price_gbp=EBAY_PRICE * 0.65)
print("Supplier result:", supplier)

if not supplier:
    print("No supplier found via find_best_supplier, building manually from ASIN")
    # Build product dict directly from Amazon ASIN
    from amazon_source import check_amazon_listing
    info = check_amazon_listing(ASIN)
    print("Amazon info:", info)
    product = {
        "title": "Baby Alive Time for School Baby Doll Set Back to School Toys",
        "supplier": "amazon",
        "supplier_sku": ASIN,
        "cost_gbp": 23.30,
        "shipping_gbp": 0.0,
        "product_url": "https://www.amazon.co.uk/dp/" + ASIN,
        "images": info.get("images", []),
        "description": "Baby Alive Time for School doll set. Back to school toys suitable for kids aged 3 years and over.",
        "category": "baby_kids",
    }
else:
    product = supplier

print()
print("Listing product:", product.get('title','')[:60])
print("Listing price: GBP" + str(EBAY_PRICE))

result = create_listing(product, listing_price_gbp=EBAY_PRICE)
print("Listing result:", result)
