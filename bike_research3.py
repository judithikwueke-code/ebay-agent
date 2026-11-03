import sys, os, requests
sys.path.insert(0, '/var/www/ebay-agent-2')
os.chdir('/var/www/ebay-agent-2')
from dotenv import load_dotenv; load_dotenv()
from amazon_source import check_amazon_listing
from supplier import _search_cj, _search_costway, _get_cj_access_token, _parse_cj_price

# ===== CJ - search all warehouses, flag UK ones =====
print("=" * 60)
print("CJ DROPSHIPPING - all warehouses, flag UK stock")
print("=" * 60)
token = _get_cj_access_token()
print("CJ token:", "OK" if token else "FAILED")

if token:
    for kw in ['mountain bike 26 inch', 'fat tire bicycle', 'kids bike girls pink', 'bicycle children']:
        r = requests.get('https://developers.cjdropshipping.com/api2.0/v1/product/list',
            headers={'CJ-Access-Token': token},
            params={'productNameEn': kw, 'pageNum': 1, 'pageSize': 30, 'countryCode': 'GB'},
            timeout=15)
        if r.status_code == 200:
            items = r.json().get('data', {}).get('list', [])
            bike_items = [i for i in items if any(w in i.get('productNameEn','').lower() 
                          for w in ['bike','bicycle','cycle','pram'])]
            if bike_items:
                print(f"\n'{kw}' -> {len(bike_items)} results:")
                for i in bike_items[:5]:
                    price = _parse_cj_price(i.get('sellPrice', 0)) or 0
                    wh = i.get('warehouseCountryCode','?')
                    title = i.get('productNameEn','')[:65]
                    flag = '🇬🇧 UK' if wh == 'GB' else f'({wh})'
                    print(f"  {flag} GBP{round(price,2)} | {title}")
        else:
            print(f"  '{kw}' -> CJ error {r.status_code}")

# ===== COSTWAY =====
print("\n" + "=" * 60)
print("COSTWAY UK bikes")
print("=" * 60)
for kw in ['mountain bike', 'fat tire bike', 'girls bike', 'kids bicycle pink', 'bicycle adult']:
    results = _search_costway(kw, 500)
    if results:
        print(f"  '{kw}' -> {len(results)} results:")
        for r in results[:3]:
            print(f"    GBP{r.get('cost_gbp','')} | {r.get('title','')[:65]}")
    else:
        print(f"  '{kw}' -> 0")

# ===== Amazon direct ASIN checks =====
print("\n" + "=" * 60)
print("AMAZON UK - direct product page checks")
print("=" * 60)
# Try various known-format UK fat tire & kids bike ASINs
asins = [
    'B0CHHFJJCH', 'B0CLBLDFKG', 'B0D1VVJBXF', 'B09KFVXVHF',
    'B0C3J3VP8C', 'B0CMRD8GBH', 'B09TVMF8T9', 'B0CY1D6XMH',
    'B0BZF4QD2G', 'B08Y5FCBMP', 'B0C3T8NGQX', 'B0DKXWWQWF',
]
for asin in asins:
    check = check_amazon_listing(asin)
    if check.get('in_stock'):
        price = check.get('price_gbp')
        title = (check.get('title') or '')[:65]
        imgs = len(check.get('images') or [])
        print(f"  IN STOCK: {asin} | GBP{price} | imgs={imgs} | {title}")
    elif check.get('blocked'):
        print(f"  BOT BLOCKED: {asin}")
