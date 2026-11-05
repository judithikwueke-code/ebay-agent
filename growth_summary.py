"""Telegram digest after a growth run: what was listed in the last N hours, what sold, quota left."""
import os
import sqlite3
import sys

os.environ.setdefault("SEASONAL_ONLY", "0")
from dotenv import load_dotenv

load_dotenv()
from db import DB_PATH

hours = int(sys.argv[1]) if len(sys.argv) > 1 else 24
c = sqlite3.connect(DB_PATH)
new = c.execute("SELECT title, listed_price_gbp, category FROM products WHERE created_at >= datetime('now', ?) "
                "ORDER BY id DESC", (f"-{hours} hours",)).fetchall()
sold = c.execute("""SELECT p.title, o.sale_price_gbp, o.profit_gbp FROM orders o JOIN products p ON p.id = o.product_id
                    WHERE o.status != 'cancelled' AND o.created_at >= datetime('now', ?) ORDER BY o.id DESC""",
                 (f"-{hours} hours",)).fetchall()
active = c.execute("SELECT count(*) FROM products WHERE status='active'").fetchone()[0]
lines = [f"GROWTH REPORT (last {hours}h)", f"  New listings: {len(new)} | Active listings: {active}",
         f"  Orders: {len(sold)} | Profit: GBP {sum(r[2] or 0 for r in sold):.2f}"]
for t, p, cat in new[:25]:
    lines.append(f"  + {t[:55]} - GBP {p:.2f} [{cat}]")
if len(new) > 25:
    lines.append(f"  ... and {len(new) - 25} more")
for t, sp, pf in sold[:10]:
    lines.append(f"  SOLD {t[:50]} GBP {sp:.2f} (profit {pf:.2f})")
try:
    import requests
    from research import _quota_left
    left = _quota_left()
    lines.append(f"  eBay API calls left today: {left}")
except Exception:
    pass
from telegram_bot import _send
_send(chr(10).join(lines), silent=True)
print(chr(10).join(lines))
