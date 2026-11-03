import sys, os, requests, json
sys.path.insert(0, '/var/www/ebay-agent-2')
os.chdir('/var/www/ebay-agent-2')
from dotenv import load_dotenv; load_dotenv()
from supplier import _get_cj_access_token, _parse_cj_price, _parse_cj_image

token = _get_cj_access_token()

# ===== Get full details on CJ mountain bike =====
print("CJ MTB FULL DETAILS")
print("=" * 60)
r = requests.get('https://developers.cjdropshipping.com/api2.0/v1/product/list',
    headers={'CJ-Access-Token': token},
    params={'productNameEn': 'mountain bike 26', 'pageNum': 1, 'pageSize': 10, 'countryCode': 'GB'},
    timeout=15)
items = r.json().get('data', {}).get('list', []) if r.status_code == 200 else []
for i in items:
    price = _parse_cj_price(i.get('sellPrice',0)) or 0
    if price > 100:
        pid = i.get('pid','')
        title = i.get('productNameEn','')
        wh = i.get('warehouseCountryCode','')
        ship_time = i.get('shippingTime','')
        ship_name = i.get('shippingName','')
        cat = i.get('categoryName','')
        img = (_parse_cj_image(i.get('imageUrl','')) or [''])[:1]
        print(f"PID: {pid}")
        print(f"Title: {title}")
        print(f"Price: GBP{price}")
        print(f"Category: {cat}")
        print(f"Warehouse: {wh}")
        print(f"Shipping time: {ship_time}")
        print(f"Shipping name: {ship_name}")
        print(f"Image: {img[0][:80] if img[0] else 'none'}")
        
        # Get product variants/details
        r2 = requests.get(f'https://developers.cjdropshipping.com/api2.0/v1/product/variant/query',
            headers={'CJ-Access-Token': token},
            params={'pid': pid}, timeout=10)
        if r2.status_code == 200:
            variants = r2.json().get('data', [])
            print(f"Variants: {len(variants)}")
            for v in variants[:3]:
                v_price = _parse_cj_price(v.get('variantSellPrice',0)) or 0
                v_wh = v.get('warehouseCountryCode','')
                v_name = v.get('variantNameEn','')
                v_stock = v.get('variantStock',0)
                print(f"  variant: {v_name} | GBP{v_price} | wh={v_wh} | stock={v_stock}")
        print()

# ===== Search specifically for UK warehouse bikes on CJ =====
print("CJ - UK WAREHOUSE ONLY filter")
print("=" * 60)
r = requests.get('https://developers.cjdropshipping.com/api2.0/v1/product/list',
    headers={'CJ-Access-Token': token},
    params={'productNameEn': 'bike', 'pageNum': 1, 'pageSize': 50,
            'countryCode': 'GB', 'warehouseCountryCode': 'GB'},
    timeout=15)
print(f"Status: {r.status_code}")
if r.status_code == 200:
    items = r.json().get('data', {}).get('list', [])
    print(f"UK warehouse bike results: {len(items)}")
    for i in items[:10]:
        price = _parse_cj_price(i.get('sellPrice',0)) or 0
        title = i.get('productNameEn','')[:65]
        wh = i.get('warehouseCountryCode','')
        print(f"  GBP{price} | wh={wh} | {title}")
else:
    print(f"Error: {r.text[:200]}")
