"""Read any product page with the shared browser: title, final URL, visible text, images. Usage: temu_probe.py <url>
(Temu itself redirects a server-side browser to its login page - only the image URLs inside a share link are public.)"""
import json
import sys

import amazon_source

url = sys.argv[1]


def _read():
    page = amazon_source._get_context().new_page()
    try:
        page.goto(url, wait_until="domcontentloaded", timeout=45000)
        page.wait_for_timeout(6000)
        return {
            "title": page.title(),
            "url": page.url,
            "text": page.evaluate("document.body.innerText.slice(0, 3500)"),
            "imgs": page.evaluate("""() => [...new Set([...document.images].map(i => i.src)
                .filter(s => s.includes('kwcdn.com') && !s.includes('icon') && !s.includes('avatar')))].slice(0, 40)"""),
            "og": page.evaluate("document.querySelector('meta[property=\"og:image\"]')?.content || ''"),
        }
    finally:
        page.close()


try:
    out = amazon_source.on_browser(_read)
finally:
    amazon_source.close()
print(json.dumps(out, ensure_ascii=False)[:6000])
