import sys, os, requests
sys.path.insert(0, '/var/www/ebay-agent-2')
os.chdir('/var/www/ebay-agent-2')
from dotenv import load_dotenv; load_dotenv()
from amazon_source import check_amazon_listing, search_amazon
from research import _market_snapshot, _browse_search
from config import CATEGORIES, get_app_token
from supplier import _search_cj, _search_costway

# ===== 1. eBay market depth for both bike types =====
print("=" * 60)
print("1. eBay MARKET DEPTH")
print("=" * 60)
sports_cat = CATEGORIES['sports_fitness']['id']

mtb_snaps = [
    ('26 inch fat tire mountain bike 7 speed', sports_cat),
    ('fat tire bike adults 26 inch', sports_cat),
    ('mountain bike fat tyre mens womens', sports_cat),
]
kids_snaps = [
    ('pink girls bike kids 14 inch', CATEGORIES['baby_kids']['id']),
    ('girls bicycle kids pink 12 14 16 inch', CATEGORIES['baby_kids']['id']),
    ('childrens bike girls pink', CATEGORIES['baby_kids']['id']),
]

print("\nMountain bike market:")
for kw, cat in mtb_snaps:
    snap = _market_snapshot(kw, cat, min_price=100, max_price=500)
    print(f"  GBP{snap['avg_price_gbp']} avg | {snap['active_listings']} sellers | '{kw}'")

print("\nKids bike market:")
for kw, cat in kids_snaps:
    snap = _market_snapshot(kw, cat, min_price=30, max_price=200)
    print(f"  GBP{snap['avg_price_gbp']} avg | {snap['active_listings']} sellers | '{kw}'")

# ===== 2. CJ Dropshipping - UK WAREHOUSE specifically =====
print("\n" + "=" * 60)
print("2. CJ DROPSHIPPING - checking UK warehouse flag")
print("=" * 60)
import json, os
CJ_EMAIL = os.getenv('CJ_EMAIL', '')
CJ_API_KEY = os.getenv('CJ_API_KEY', '')

# Direct CJ API search with warehouse filter
def cj_search_uk(keyword, max_price=500):
    token_r = requests.post('https://developers.cjdropshipping.com/api2.0/v1/authentication/getAccessToken',
        json={'email': CJ_EMAIL, 'password': os.getenv('CJ_API_KEY','')}, timeout=10)
    if token_r.status_code != 200: return []
    token = token_r.json().get('data', {}).get('accessToken','')
    if not token: return []
    
    r = requests.get('https://developers.cjdropshipping.com/api2.0/v1/product/list',
        headers={'CJ-Access-Token': token},
        params={'productNameEn': keyword, 'pageNum': 1, 'pageSize': 20,
                'countryCode': 'GB', 'warehouseCountryCode': 'GB'},
        timeout=15)
    if r.status_code != 200: return []
    items = r.json().get('data', {}).get('list', [])
    out = []
    for i in items:
        price = float(i.get('sellPrice', 0) or 0)
        if 0 < price <= max_price:
            out.append({
                'id': i.get('pid',''),
                'title': i.get('productNameEn','')[:70],
                'price': round(price, 2),
                'warehouse': i.get('warehouseCountryCode',''),
                'shipping': i.get('shippingTime',''),
            })
    return out

for kw in ['mountain bike', 'fat tire bicycle adult', 'girls bicycle kids', 'pink bicycle']:
    results = cj_search_uk(kw, 400)
    if results:
        print(f"\n  '{kw}' UK warehouse ({len(results)} results):")
        for r in results[:4]:
            print(f"    GBP{r['price']} | warehouse={r['warehouse']} | {r['title']}")
    else:
        print(f"  '{kw}' -> 0 UK warehouse results")

# ===== 3. Costway UK =====
print("\n" + "=" * 60)
print("3. COSTWAY UK")
print("=" * 60)
for kw in ['mountain bike', 'girls bike kids', 'fat tire bike']:
    results = _search_costway(kw, 400)
    if results:
        print(f"  '{kw}' -> {len(results)} results")
        for r in results[:3]:
            print(f"    GBP{r.get('cost_gbp','')} | {r.get('title','')[:65]}")
    else:
        print(f"  '{kw}' -> 0 results")

# ===== 4. Amazon direct ASIN checks (known fat tire bike ASINs) =====
print("\n" + "=" * 60)
print("4. AMAZON DIRECT ASIN CHECKS")
print("=" * 60)
# Well-known UK fat tire bike and kids bike ASINs
test_asins = [
    ('B0CHHFJJCH', '26" fat tire MTB candidate'),
    ('B0BZF4QD2G', '26" MTB candidate'),
    ('B09TVMF8T9', '26" fat bike candidate'),
    ('B0C3T8NGQX', 'Girls 14" bike candidate'),
    ('B08Y5FCBMP', 'Girls kids bike candidate'),
    ('B0CMRD8GBH', 'Kids bike candidate'),
]
for asin, note in test_asins:
    check = check_amazon_listing(asin)
    if check.get('in_stock'):
        price = check.get('price_gbp')
        title = check.get('title','')[:60]
        imgs = len(check.get('images',[]))
        print(f"  IN STOCK: {asin} GBP{price} imgs={imgs} | {title}")
    else:
        print(f"  {note}: in_stock={check.get('in_stock')} | {asin}")
