import sys, os
sys.path.insert(0, '/var/www/ebay-agent-2')
os.chdir('/var/www/ebay-agent-2')
from dotenv import load_dotenv; load_dotenv()
from amazon_source import search_amazon, check_amazon_listing

# Check stock on Baby Alive candidates found
candidates = [
    ('B0B9HPHNJ3', 31.69, 'Baby Alive Lil Snacks Doll'),
    ('B0BDHM2XHM', 23.30, 'Baby Alive Time for School'),
]

EBAY_MARKET_PRICE = 45.38

print("eBay market avg: GBP" + str(EBAY_MARKET_PRICE))
print()

for asin, cost, title in candidates:
    check = check_amazon_listing(asin)
    in_stock = check.get('in_stock')
    blocked = check.get('blocked')
    margin = round(EBAY_MARKET_PRICE - cost - (EBAY_MARKET_PRICE * 0.109) - 0.30, 2)
    print("ASIN:", asin)
    print("  Title:", title)
    print("  Amazon cost: GBP" + str(cost))
    print("  In stock:", in_stock, "| Blocked:", blocked)
    print("  Est margin at market price GBP" + str(EBAY_MARKET_PRICE) + ":", "GBP" + str(margin))
    print()
