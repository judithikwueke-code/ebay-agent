import sys, os, logging
sys.path.insert(0, '/var/www/ebay-agent-2')
os.chdir('/var/www/ebay-agent-2')
from dotenv import load_dotenv; load_dotenv()
logging.basicConfig(level=logging.INFO, format='%(levelname)s - %(message)s')

from lister import create_listing
from db import is_product_already_listed
from config import CATEGORIES

ASIN = 'B0BDHM2XHM'
KEYWORD = 'baby alive doll feeding'

# Check not already listed
if is_product_already_listed('amazon', ASIN):
    print("Already listed, nothing to do.")
    sys.exit(0)

cat_info = CATEGORIES['baby_kids']

opportunity = {
    'keyword': KEYWORD,
    'category': 'baby_kids',
    'category_id': cat_info['id'],
    'avg_price_gbp': 45.38,
    'active_listings': 5,
}

supplier_match = {
    'supplier': 'amazon',
    'product_id': ASIN,
    'title': 'Baby Alive Time for School Baby Doll Set Back to School Toys for Kids',
    'cost_gbp': 23.30,
    'shipping_gbp': 0.0,
    'product_url': 'https://www.amazon.co.uk/dp/' + ASIN,
    'images': ['https://m.media-amazon.com/images/I/71bmB0nNIeL.jpg'],
    'description': 'Baby Alive Time for School doll set. Interactive baby doll with school accessories. Suitable for ages 3 years and above. Doll eats, drinks and has a nappy change.',
    'margin_pct': round((42.99 - 23.30 - 42.99 * 0.109 - 0.30) / 42.99 * 100, 1),
    'total_cost_gbp': 23.30,
    'uk_warehouse': True,
    'delivery_days': 2,
}

print("Listing Baby Alive Time for School doll at target GBP42.99...")
result = create_listing(opportunity, supplier_match, target_sell_price_gbp=42.99)
print("Result:", result)
