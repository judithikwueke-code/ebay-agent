"""Owner's rule: ALWAYS pass an Amazon price rise on, NEVER lower a price, pause only when out of stock.
    python test_reprice.py [--staged]      (server; --staged tests /tmp/ea2-new instead of the live file)
"""
import os
import sys

os.chdir("/var/www/ebay-agent-2")
sys.path.insert(0, "/var/www/ebay-agent-2")
if "--staged" in sys.argv:
    sys.path.insert(0, "/tmp/ea2-new")
import stock_monitor as sm  # noqa: E402

print("testing", sm.__file__)
fails = 0


def check(name, ok, detail=""):
    global fails
    fails += 0 if ok else 1
    print(f"{'ok  ' if ok else 'FAIL'} {name} {detail}")


VAT = 0.2
# hose reel: on file 38.83 ex-VAT = 46.60 inc VAT, listed 56.99, Amazon now 85.30 (+38.70, +83%)
p, why = sm._auto_reprice(361, "B0X", "Retractable Hose Reel", 56.99, 38.83, 85.30, VAT)
check("huge rise is passed on, not refused", p is not None and p > 56.99 + 38.0, f"-> {p} | {why[:70] if why else why}")
check("the note says it is a big rise", bool(why) and why.startswith("BIG rise"))
profit_before = 56.99 * 0.976 - 46.60
profit_after = p * 0.976 - 85.30
check("planned profit kept", abs(profit_after - profit_before) < 0.1, f"{profit_before:.2f} -> {profit_after:.2f}")
p, why = sm._auto_reprice(1, "B0X", "Small rise", 10.99, 6.67, 8.99, VAT)   # on file 8.00 inc VAT -> 8.99
check("small rise passed on", p is not None and p > 10.99, f"-> {p}")
p, why = sm._auto_reprice(2, "B0X", "Amazon price fell", 20.99, 14.17, 12.00, VAT)   # on file 17.00 -> 12.00
check("a fall NEVER lowers the price", p is None and why is None)
p, why = sm._auto_reprice(3, "B0X", "Unchanged", 20.99, 14.17, 17.00, VAT)
check("unchanged -> nothing", p is None)
check("price floor for GBP 3 profit at Amazon 85.30", sm._price_for_profit(85.30) in (90.49, 90.99), f"-> {sm._price_for_profit(85.30)}")
check("price floor is a .49/.99 price", round(sm._price_for_profit(13.99) % 1, 2) in (0.49, 0.99), f"-> {sm._price_for_profit(13.99)}")
src = open(sm.__file__).read()
check("monitor pauses only when out of stock", 'if status == "active" and not res["in_stock"]:' in src)
check("no 'too big' refusal left", "too big to auto-reprice" not in src)
print("FAILS:", fails)
