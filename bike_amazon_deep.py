import sys, os, requests
sys.path.insert(0, '/var/www/ebay-agent-2')
os.chdir('/var/www/ebay-agent-2')
from dotenv import load_dotenv; load_dotenv()
from amazon_source import search_amazon, check_amazon_listing

print("Amazon search - kids pink bikes:")
searches = [
    ('Pink Little Princess girls bike kids', 100.0),
    ('girls pink bike 14 inch stabilisers', 100.0),
    ('kids bicycle girls pink 12 inch', 100.0),
    ('children bicycle girls pink stabilizers', 90.0),
    ('26 inch fat tire mountain bike adults', 350.0),
    ('fat tyre mountain bike 7 speed mens', 350.0),
    ('adult mountain bike 26 fat tyre bicycle', 350.0),
]
all_found = {}
for kw, max_p in searches:
    results = search_amazon(kw, max_p)
    for r in results:
        asin = r.get('product_id','')
        if asin and asin not in all_found:
            all_found[asin] = r
    if results:
        print(f"  '{kw}' -> {len(results)}: " + ", ".join(f"{r.get('product_id','')} GBP{r.get('cost_gbp','')}" for r in results[:3]))
    else:
        print(f"  '{kw}' -> 0")

print(f"\nTotal unique found: {len(all_found)}")
print()

# Margin analysis
EBAY_MTB = 259.90
EBAY_KIDS = 87.00  # market avg (not the cheapest £75 listing)
VAT = 1.20

print("Margin analysis:")
for asin, r in all_found.items():
    retail = r.get('cost_gbp', 0)
    title = r.get('title','')[:65]
    is_kids = any(w in title.lower() for w in ['girl','kid','child','princess','pink','toddler'])
    ebay_sell = (EBAY_KIDS * 0.97) if is_kids else (EBAY_MTB * 0.97)
    ex_vat = round(retail / VAT, 2)
    margin = round(ebay_sell - retail - ebay_sell * 0.109 - 0.30, 2)
    tag = 'OK' if margin >= 5 else 'THIN'
    print(f"  [{tag}] {asin} | Amazon GBP{retail} | sell GBP{round(ebay_sell,2)} | margin GBP{margin} | {title[:55]}")

# Try checking stock on some known-pattern ASINs  
print("\nDirect ASIN stock checks (kids bikes):")
kids_asins = [
    'B0B3DRKG8B', 'B0B7YNLT7T', 'B09VKTFKQF', 'B09W2RMSTV',
    'B0BFLMR2D9', 'B0BFY8TMJQ', 'B0C8FTPR1L', 'B09X4B18KC',
    'B0D5R9TYCR', 'B0DKTN2QMB', 'B0DLHS4PW4', 'B0DLHPBQMK',
]
for asin in kids_asins:
    check = check_amazon_listing(asin)
    if check.get('in_stock'):
        p = check.get('price_gbp')
        t = (check.get('title') or '')[:60]
        i = len(check.get('images') or [])
        if p and p < 90:
            margin = round(EBAY_KIDS*0.97 - p - EBAY_KIDS*0.97*0.109 - 0.30, 2)
            print(f"  IN STOCK: {asin} GBP{p} | margin GBP{margin} | imgs={i} | {t}")
