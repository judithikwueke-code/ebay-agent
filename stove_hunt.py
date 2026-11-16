"""Stoves sold by Amazon UK with PRIME (fast, free delivery) that earn at least GBP 10 a sale on eBay.

    python stove_hunt.py            # dry run: reads Amazon, prices every candidate, lists NOTHING (writes /tmp/stove_plan.json)
    python stove_hunt.py --list     # same search, then lists what qualifies (max --max N, default 8)

A candidate must: carry the Prime badge in the search results; show a real delivery date within AMAZON_MAX_DELIVERY_DAYS
(7) on its own product page; be in stock; not be an accessory/fuel; have an eBay price that clears the profit floor
(price * 0.976 - Amazon price delivered >= MIN_PROFIT) AND is not more than 10% above the average eBay price for the
same search (otherwise it would not sell). One browser, under the agent's browser lock.
"""
import fcntl
import json
import os
import re
import sys
import time

os.chdir("/var/www/ebay-agent-2")
sys.path.insert(0, "/var/www/ebay-agent-2")
os.environ.setdefault("DAILY_LISTING_CAP", "150")
os.environ["SEASONAL_ONLY"] = "0"

MIN_PROFIT = float(os.getenv("STOVE_MIN_PROFIT", "10"))
MAX_ABOVE_MARKET = 1.10
KEEP = 1 - 0.02 * 1.2                      # what is left of the price after the 2% promoted-listing fee and VAT on it
LIST = "--list" in sys.argv
MAX_LIST = int(sys.argv[sys.argv.index("--max") + 1]) if "--max" in sys.argv else 8
MAX_PAGES = 26                             # product pages read in one run (memory and Amazon politeness)
PLAN = "/tmp/stove_plan.json"

KEYWORDS = ["camping stove", "portable gas stove", "butane stove", "cassette gas stove", "multi fuel camping stove",
            "wood burning camping stove", "gas camping stove 2 burner", "portable gas cooker", "tent stove wood burner",
            "backpacking stove"]
# not the stove itself
NOT_A_STOVE = re.compile(r"\b(?:cover|case|bag|pouch|windshield|wind ?shield|fuel|canister|cartridge|gas bottle|refill|"
                         r"adapter|adaptor|regulator|hose|igniter|lighter|brush|cleaner|polish|paint|glass|door|gasket|"
                         r"rope|rack|stand only|spare|replacement|pot|pan|kettle|cookware|mess kit|tablets?|solid fuel|"
                         r"firelighters?|bbq|barbecue|grill|pizza|fan|chimney|flue|pipe|kit)\b", re.I)

HOBS = "--hobs" in sys.argv                           # portable induction hobs / cooktops / hot plates instead of stoves
if HOBS:
    KEYWORDS = ["Daewoo induction hob", "portable induction hob", "single induction hob", "portable induction cooktop",
                "double induction hob portable", "portable electric hob", "electric hot plate portable",
                "infrared cooker portable", "ceramic hob portable"]
    NOT_A_STOVE = re.compile(NOT_A_STOVE.pattern.replace("|glass|", "|").replace("|pot|pan|", "|").replace("|kit)", ")")
                             .replace("|fan|", "|fan|extractor|"), re.I)
ACCEPT = re.compile(r"\bhobs?\b|\bcook ?tops?\b|\bhot ?plates?\b|\bcookers?\b|\binduction\b" if HOBS
                    else r"\bstoves?\b|\bcookers?\b|\bburner\b", re.I)

lock = open("/tmp/growth.lock", "a")
try:
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
except OSError:
    sys.exit("another browser job holds the lock - not started")
avail = next(int(l.split()[1]) // 1024 for l in open("/proc/meminfo") if l.startswith("MemAvailable"))
if avail < 700:
    sys.exit(f"only {avail} MB of memory available - not starting a browser")

import amazon_source as a  # noqa: E402
from deal_scan import _nice  # noqa: E402
from seed_amazon import prepare, SeedSkip  # noqa: E402
from db import count_listings_today  # noqa: E402
import lister  # noqa: E402

IGNORE_MARKET = "--ignore-market" in sys.argv      # owner's call: price for the profit, whatever other sellers charge
PRIME_ONLY = "--prime-only" in sys.argv             # the page itself must show the Prime badge
ONLY = {}                                           # --only ASIN:keyword:amazon price,...  (skip the search, read just these)
if "--only" in sys.argv:
    for part in sys.argv[sys.argv.index("--only") + 1].split(","):
        asin_, kw_, price_ = part.split(":")
        ONLY[asin_] = {"product_id": asin_, "title": kw_, "cost_gbp": float(price_), "kw": kw_, "delivery_days": 3}

found, rows = {}, []
t0 = time.time()
try:
    for kw in ([] if ONLY else KEYWORDS):
        res = a.search_amazon(kw, 400, limit=12, strict=False, min_reviews=30)
        n_new = 0
        for c in res:
            asin = c["product_id"]
            if asin in found:
                continue
            found[asin] = dict(c, kw=kw)
            n_new += 1
        print(f"search '{kw}': {len(res)} result(s), {n_new} new", flush=True)
        time.sleep(1.5)
    found.update(ONLY)
    cards_prime = sum(1 for v in found.values() if v["delivery_days"] == 3)   # 3 = the Prime badge showed on the card
    print(f"\n{len(found)} distinct products, {cards_prime} with a Prime badge on the search card "
          f"(the product page decides below)", flush=True)
    todo = []
    for asin, c in found.items():
        if not ONLY and (NOT_A_STOVE.search(c["title"]) or not ACCEPT.search(c["title"])):
            rows.append(dict(asin=asin, title=c["title"][:70], verdict="skip", why="not a stove (accessory / other product)"))
            continue
        todo.append((asin, c))
    # cheapest first would waste pages on cheap stoves that cannot make GBP 10: start with the dearer ones
    todo.sort(key=lambda x: -x[1]["cost_gbp"])
    for asin, c in todo[:MAX_PAGES]:
        kw = c["kw"]
        need = _nice((c["cost_gbp"] + MIN_PROFIT) / KEEP, True)
        row = dict(asin=asin, title=c["title"][:70], search_price=c["cost_gbp"], kw=kw, need_price=need)
        try:
            plan = prepare(asin, kw, "any", price=need)
        except SeedSkip as e:
            row.update(verdict="skip", why=str(e)[:150])
            rows.append(row)
            print(f"  skip {asin} - {row['why']}", flush=True)
            continue
        prod, market = plan["prod"], plan["market"]
        shelf = plan["shelf"] + prod["shipping_gbp"] * 1.2
        price = _nice((shelf + MIN_PROFIT) / KEEP, True)
        profit = round(price * KEEP - shelf, 2)
        row.update(shelf=shelf, price=price, profit=profit, market=market, delivery=prod.get("delivery_text", ""),
                   delivery_date=prod.get("delivery_date"), prime=prod.get("prime"), title_full=prod["title"])
        dtext = prod.get("delivery_text", "")
        fast = prod.get("prime") or bool(re.search(r"prime|free", dtext, re.I))   # Prime badge, or free delivery with a date
        row["prime_badge"] = prod.get("prime")
        if not prod.get("delivery_date"):
            row.update(verdict="skip", why="Amazon shows no delivery date on the page")
        elif PRIME_ONLY and not (prod.get("prime") or ("free" in dtext.lower() and "first eligible" not in dtext.lower())):
            row.update(verdict="skip", why="no Prime badge on the product page")   # free delivery 'on your first order' is not Prime
        elif not fast:
            row.update(verdict="skip", why="not Prime and no free delivery on the product page")
        elif profit < MIN_PROFIT - 0.005:
            row.update(verdict="skip", why=f"profit GBP {profit:.2f}")
        elif not IGNORE_MARKET and price > market * MAX_ABOVE_MARKET:
            row.update(verdict="skip", why=f"GBP {price:.2f} needed for GBP {MIN_PROFIT:.0f} profit, eBay average is GBP {market:.2f}")
        else:
            row.update(verdict="OK", why="qualifies")
            row["_plan"] = plan
        rows.append(row)
        print(f"  {row['verdict']:4s} {asin} Amazon £{shelf:.2f} -> eBay £{price:.2f} (profit £{profit:.2f}, market £{market:.2f}) "
              f"prime={prod.get('prime')} due {row['delivery_date']} '{row['delivery'][:45]}' | {c['title'][:45]} | "
              f"{row['why'] if row['verdict'] != 'OK' else ''}", flush=True)
        time.sleep(1.0)

    ok = [r for r in rows if r["verdict"] == "OK"]
    json.dump([{k: v for k, v in r.items() if k != "_plan"} for r in rows], open(PLAN, "w"), indent=1, default=str)
    print(f"\n{len(ok)} qualify; {len(rows)} judged; {time.time() - t0:.0f}s; plan in {PLAN}", flush=True)
    if LIST:
        lister.DAILY_LISTING_CAP = max(lister.DAILY_LISTING_CAP, count_listings_today() + MAX_LIST + 1)
        done = 0
        for r in sorted(ok, key=lambda r: -r["profit"])[:MAX_LIST]:
            plan = r["_plan"]
            lid = lister.create_listing(plan["opp"], plan["prod"], target_sell_price_gbp=r["price"])
            print(("LISTED " if lid else "REFUSED"), r["asin"], lid, f"£{r['price']:.2f}", r["title"][:50], flush=True)
            done += 1 if lid else 0
        print(f"{done} listed", flush=True)
finally:
    a.close()
    lock.close()
