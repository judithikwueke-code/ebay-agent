import sys, os, requests, json
sys.path.insert(0, '/var/www/ebay-agent-2')
os.chdir('/var/www/ebay-agent-2')
from dotenv import load_dotenv; load_dotenv()
from amazon_source import check_amazon_listing
from supplier import _search_costway

# ===== CJ UK Warehouse =====
print("=" * 60)
print("CJ DROPSHIPPING - UK Warehouse bikes")
print("=" * 60)

CJ_EMAIL = os.getenv('CJ_EMAIL', '')
CJ_PASSWORD = os.getenv('CJ_PASSWORD', '')

token_r = requests.post('https://developers.cjdropshipping.com/api2.0/v1/authentication/getAccessToken',
    json={'email': CJ_EMAIL, 'password': CJ_PASSWORD}, timeout=10)
print("CJ auth:", token_r.status_code)
token = token_r.json().get('data', {}).get('accessToken','') if token_r.status_code == 200 else ''

if token:
    for kw in ['mountain bike', 'bicycle adult', 'girls bike', 'kids bicycle pink']:
        r = requests.get('https://developers.cjdropshipping.com/api2.0/v1/product/list',
            headers={'CJ-Access-Token': token},
            params={'productNameEn': kw, 'pageNum': 1, 'pageSize': 20, 'countryCode': 'GB'},
            timeout=15)
        if r.status_code == 200:
            items = r.json().get('data', {}).get('list', [])
            bike_items = [i for i in items if 'bike' in i.get('productNameEn','').lower() 
                         or 'bicycle' in i.get('productNameEn','').lower()
                         or 'cycle' in i.get('productNameEn','').lower()]
            if bike_items:
                print(f"\n'{kw}' -> {len(bike_items)} bike items:")
                for i in bike_items[:4]:
                    price_raw = str(i.get('sellPrice','0'))
                    price = float(price_raw.split('--')[0].strip()) if '--' in price_raw else float(price_raw or 0)
                    warehouse = i.get('warehouseCountryCode','CN')
                    title = i.get('productNameEn','')[:65]
                    print(f"  GBP{round(price,2)} | warehouse={warehouse} | {title}")
            else:
                print(f"'{kw}' -> no bike results")

# ===== COSTWAY UK =====
print("\n" + "=" * 60)
print("COSTWAY UK bikes")
print("=" * 60)
for kw in ['mountain bike', 'kids bike girls', 'fat tire bicycle', 'girls bicycle']:
    results = _search_costway(kw, 500)
    if results:
        print(f"  '{kw}' -> {len(results)} results:")
        for r in results[:3]:
            print(f"    GBP{r.get('cost_gbp','')} | {r.get('title','')[:65]}")
    else:
        print(f"  '{kw}' -> 0 results")

# ===== Amazon direct checks =====
print("\n" + "=" * 60)
print("AMAZON UK - direct ASIN checks")
print("=" * 60)
# Common UK fat tire MTB & kids bike ASINs on Amazon
test_asins = [
    'B0CHHFJJCH', 'B0BZF4QD2G', 'B09TVMF8T9', 'B0C3J3VP8C',
    'B0CMRD8GBH', 'B08Y5FCBMP', 'B0C3T8NGQX', 'B09KFVXVHF',
    'B0D1VVJBXF', 'B0CLBLDFKG',
]
for asin in test_asins:
    check = check_amazon_listing(asin)
    if check.get('in_stock'):
        price = check.get('price_gbp')
        title = check.get('title','')[:65]
        imgs = len(check.get('images',[]))
        print(f"  IN STOCK: {asin} GBP{price} | imgs={imgs} | {title}")
