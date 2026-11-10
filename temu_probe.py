"""Read a Temu product page with the shared browser: title, price, images, visible text. Usage: temu_probe.py <url>"""
import json
import re
import sys
import time

import amazon_source

url = sys.argv[1]
page = amazon_source._get_context().new_page()
out = {}
try:
    page.goto(url, wait_until="domcontentloaded", timeout=45000)
    page.wait_for_timeout(6000)
    out["title"] = page.title()
    out["url"] = page.url
    out["text"] = page.evaluate("document.body.innerText.slice(0, 3500)")
    out["imgs"] = page.evaluate("""() => [...new Set([...document.images].map(i => i.src)
        .filter(s => s.includes('kwcdn.com') && !s.includes('icon') && !s.includes('avatar')))].slice(0, 40)""")
    out["og"] = page.evaluate("document.querySelector('meta[property=\"og:image\"]')?.content || ''")
finally:
    page.close()
    amazon_source.close()
print(json.dumps(out, ensure_ascii=False)[:6000])
