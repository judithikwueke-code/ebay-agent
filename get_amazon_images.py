import sys, os
sys.path.insert(0, '/var/www/ebay-agent-2')
os.chdir('/var/www/ebay-agent-2')
from dotenv import load_dotenv; load_dotenv()
from amazon_source import check_amazon_listing

ASIN = 'B0BDHM2XHM'
info = check_amazon_listing(ASIN)
print("in_stock:", info.get('in_stock'))
print("images:", info.get('images', []))
print("title:", info.get('title', '')[:80])
print("price:", info.get('price_gbp'))
