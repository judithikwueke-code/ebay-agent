import sys, os, requests
sys.path.insert(0, '/var/www/ebay-agent-2')
os.chdir('/var/www/ebay-agent-2')
from dotenv import load_dotenv; load_dotenv()
from supplier import _search_avasam
from amazon_source import check_amazon_listing

# ===== 1. Avasam =====
print("=" * 60)
print("AVASAM - bikes")
print("=" * 60)
for kw in ['mountain bike', 'fat tire bike adult', 'kids girls bike pink', 'childrens bicycle pink', 'girls bike']:
    results = _search_avasam(kw, 400)
    if results:
        print(f"  '{kw}' -> {len(results)} results:")
        for r in results[:3]:
            print(f"    GBP{r.get('cost_gbp','')} | {r.get('title','')[:65]}")
    else:
        print(f"  '{kw}' -> 0")

# ===== 2. Amazon - brand-specific known ASINs =====
print("\n" + "=" * 60)
print("AMAZON - known brand kids bikes & MTBs")
print("=" * 60)

# Real Amazon UK bike ASINs (Apollo, Raleigh, Claud Butler, Huffy, Schwinn)
known_asins = [
    # Apollo girls bikes (very common on Amazon UK)
    'B08P94Q84G', 'B09FXHM9NJ', 'B08P95FLBX', 'B0CN5J3JZ6',
    # Raleigh kids
    'B07CSCB8R4', 'B07CSDQM6Z', 'B08CF76WC1',
    # Generic kids/fat tire
    'B09KFVXVHF', 'B09KFW2CMV', 'B0BJRYH5KV', 'B0C3J3VP8C',
    # Fat tire MTB on Amazon UK
    'B09QDQR2SP', 'B09QDT5HST', 'B0C5BFZDM1', 'B0C5BGK3F6',
    'B0BYLSSGDW', 'B0BYM1CJND', 'B0CLBLDFKG', 'B0CY1D6XMH',
]

EBAY_MTB = 259.90
EBAY_KIDS = 87.00
VAT = 1.20

found_bikes = []
for asin in known_asins:
    check = check_amazon_listing(asin)
    if check.get('in_stock') and not check.get('blocked'):
        p = check.get('price_gbp')
        t = (check.get('title') or '')[:65]
        imgs = len(check.get('images') or [])
        if p:
            is_kids = p < 120
            ebay_sell = round((EBAY_KIDS if is_kids else EBAY_MTB) * 0.97, 2)
            margin = round(ebay_sell - p - ebay_sell * 0.109 - 0.30, 2)
            tag = 'OK' if margin >= 4 else 'THIN'
            print(f"  [{tag}] {asin} | GBP{p} | sell GBP{ebay_sell} | margin GBP{margin} | imgs={imgs} | {t[:55]}")
            if margin >= 4:
                found_bikes.append({'asin': asin, 'retail': p, 'sell': ebay_sell,
                                    'margin': margin, 'title': t,
                                    'images': check.get('images',[])})

print(f"\nViable bikes found: {len(found_bikes)}")
