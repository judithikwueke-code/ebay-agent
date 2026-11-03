import sys, os, requests
sys.path.insert(0, '/var/www/ebay-agent-2')
os.chdir('/var/www/ebay-agent-2')
from dotenv import load_dotenv; load_dotenv()
import sqlite3
from db import DB_PATH

conn = sqlite3.connect(DB_PATH)
rows = conn.execute("""
    SELECT id, ebay_listing_id, supplier_product_id, title, image_urls
    FROM products
    WHERE status='active'
    ORDER BY created_at DESC LIMIT 20
""").fetchall()
conn.close()

print("Recent listings (image status):")
for row in rows:
    pid, ebay_id, asin, title, imgs = row
    img_list = [i.strip() for i in (imgs or '').split(',') if i.strip()]
    ok = []
    bad = []
    for url in img_list[:2]:
        try:
            r = requests.head(url, timeout=5, allow_redirects=True)
            if r.status_code == 200:
                ok.append(url[:60])
            else:
                bad.append((url[:60], r.status_code))
        except Exception as e:
            bad.append((url[:60], str(e)))
    status = 'OK' if ok and not bad else ('NO_IMAGES' if not img_list else 'BAD')
    print(f"  [{status}] {ebay_id} | {asin} | {title[:45]}")
    if bad:
        for u, s in bad:
            print(f"    BAD: {u} -> {s}")
    if not img_list:
        print(f"    (no image URLs stored)")
