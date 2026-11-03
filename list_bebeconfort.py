import sys, os, logging
sys.path.insert(0, '/var/www/ebay-agent-2')
os.chdir('/var/www/ebay-agent-2')
from dotenv import load_dotenv; load_dotenv()
logging.basicConfig(level=logging.INFO, format='%(levelname)s - %(message)s')

from lister import create_listing
from db import is_product_already_listed
from config import CATEGORIES

ASIN = 'B0GF2T3477'
cat_info = CATEGORIES['baby_kids']

if is_product_already_listed('amazon', ASIN):
    print("Already listed")
    sys.exit(0)

opportunity = {
    'keyword': 'compact baby buggy lightweight travel pushchair',
    'category': 'baby_kids',
    'category_id': cat_info['id'],
    'avg_price_gbp': 86.99,
    'active_listings': 5,
}
supplier_match = {
    'supplier': 'amazon',
    'product_id': ASIN,
    'title': 'Bebeconfort Snow Compact Baby Buggy Lightweight Travel Pushchair 0-15kg',
    'cost_gbp': 49.99,
    'shipping_gbp': 0.0,
    'total_cost_gbp': 49.99,
    'product_url': 'https://www.amazon.co.uk/dp/' + ASIN,
    'images': ['https://m.media-amazon.com/images/I/71Wz-w9OZML._AC_SL1500_.jpg'],
    'description': 'Bebeconfort Snow compact baby buggy. Suitable from birth to 15kg. Lightweight and compact design with easy one-hand fold. Ideal for travel and day trips.',
    'margin_pct': round((91.99 - 49.99 - 91.99 * 0.109 - 0.30) / 91.99 * 100, 1),
    'uk_warehouse': True,
    'delivery_days': 2,
}

print("Listing Bebeconfort at GBP91.99...")
result = create_listing(opportunity, supplier_match, target_sell_price_gbp=91.99)
print("Result:", result)
