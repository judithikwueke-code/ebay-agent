import sys, os
sys.path.insert(0, '/var/www/ebay-agent-2')
os.chdir('/var/www/ebay-agent-2')
from dotenv import load_dotenv; load_dotenv()
from amazon_source import search_amazon, check_amazon_listing

# Try to get real images for GLOKID and Bebeconfort via search
searches = [
    ('GLOKID 3 in 1 prams travel system', 200.0),
    ('Bebeconfort Snow baby buggy', 100.0),
    ('compact baby buggy bebeconfort', 100.0),
]
for kw, max_p in searches:
    results = search_amazon(kw, max_p)
    for r in results[:5]:
        asin = r.get('product_id', '')
        imgs = r.get('images', [])
        title = r.get('title', '')[:60]
        if asin and imgs:
            print(asin, imgs[0][:80], '|', title)
