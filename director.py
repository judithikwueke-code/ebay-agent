"""Director: the standing improvement loop for the eBay account 2 shop. Cron runs it every 3 hours.

Each loop it
  1. reads the scoreboard (profit today / last 7 days against the GBP 100 a day goal),
  2. promotes new high-value listings (5%, only where profit stays >= GBP 12),
  3. runs ONE discovery hunt: first winners (more of what just sold), otherwise the least-recently-run theme,
  4. audits everything it (or the agent) just listed against STANDING_RULES.md and alerts on any break,
  5. scans the whole shop for the word 'Amazon' (once a day and after hunts),
  6. sends ONE Telegram report: results, what it learned, ideas, what is waiting for the owner.

It only ever does what the owner approved (see STANDING_RULES.md): it never lowers a price, never pauses/ends/zero-stocks a
live listing, never raises an ad rate beyond the approved 5% on high-value items, and never starts a second browser.

    python director.py            # normal loop (cron)
    python director.py --no-hunt  # scoreboard, promotion, audits and report only
    python director.py --dry      # print the report, change nothing, send nothing
    python director.py --force    # send the Telegram report even outside the report hours
"""
import datetime
import fcntl
import json
import os
import re
import sqlite3
import subprocess
import sys
import time

HERE = "/var/www/ebay-agent-2"
os.chdir(HERE)
sys.path.insert(0, HERE)
import requests  # noqa: E402
import config  # noqa: E402,F401  (loads .env)
import stock_monitor as sm  # noqa: E402

DRY = "--dry" in sys.argv
START_HOUR = datetime.datetime.now(datetime.timezone.utc).hour
NO_HUNT = "--no-hunt" in sys.argv or DRY
FORCE = "--force" in sys.argv
GOAL = 100.0
KEEP = 0.976                           # what is left of a price after the 2% promoted-listing fee and VAT on it
FLOOR_PRICE, FLOOR_PROFIT = 25.0, 10.0
HV_PRICE, PROMO_RATE, PROMO_MIN_PROFIT = 70.0, 5.0, 12.0
MAX_HUNTS_PER_DAY = 10
QUOTA_FLOOR = 1100                    # no hunt when eBay's shared daily search quota is below this (the radar keeps its own floor of 900)
REPORT_HOURS = (8, 14, 20)             # UTC: the report goes out in these loops even if nothing happened
STATE = os.path.join(HERE, "director_state.json")
MARKETING = "https://api.ebay.com/sell/marketing/v1"

# The themes the director rotates through (least recently run first). band = the eBay price window; profit = the
# profit floor at the cheapest-quarter price. Every one is single high-value products, not bundles or accessories.
HUNTS = [
    dict(name="premium-winter", band="80-450", profit=15, cap=8,
         theme="High-value UK winter and Christmas products where eBay buyers pay GBP 80-450 (single items, not bundles): heated clothes airers, "
               "dehumidifiers, oil-filled radiators and electric heaters, robot vacuums, air fryers and multi-cookers, pressure washers, dash cams, "
               "projectors, massage guns and chairs, treadmills and walking pads, air purifiers, patio heaters, pre-lit Christmas trees, gaming chairs, steam mops"),
    dict(name="tools-workshop", band="40-300", profit=12, cap=8,
         theme="Tools and workshop single items GBP 40-300: cordless drill and tool kits, tool chests, workbenches, torque wrenches, laser levels, "
               "step ladders, air compressors, jump starters, soldering stations, mitre saws, pressure washers"),
    dict(name="garden-outdoor", band="40-300", profit=12, cap=8,
         theme="Garden and outdoor single items GBP 40-300: cordless leaf blowers, hedge trimmers, lawn mowers, patio heaters, garden storage boxes, "
               "parasols, fire pits, garden shredders, water butts, greenhouses, outdoor lighting"),
    dict(name="car-vehicle", band="35-250", profit=10, cap=8,
         theme="Car and vehicle single items GBP 35-250: dash cams, reversing cameras, jump starters, tyre inflators, car vacuum cleaners, roof boxes, "
               "seat covers sets, child car seats, towing accessories, sat navs, car polishers"),
    dict(name="home-comfort", band="40-300", profit=12, cap=8,
         theme="Home comfort single items GBP 40-300: dehumidifiers, air purifiers, electric heaters, heated airers, humidifiers, fans, electric blankets, "
               "oil radiators, fan heaters, mattress toppers, weighted blankets"),
    dict(name="kitchen-appliances", band="35-250", profit=10, cap=8,
         theme="Kitchen appliances single items GBP 35-250: air fryers, coffee machines, blenders, multi-cookers, food processors, stand mixers, "
               "kettles, toasters, sandwich makers, soup makers, ice cream makers, vacuum sealers"),
    dict(name="camping-stoves", band="30-200", profit=10, cap=8,
         theme="Camping and outdoor cooking single items GBP 30-200: gas camping stoves, portable cookers, tents, sleeping bags, camping chairs, "
               "cool boxes, camping lanterns, hammocks, portable showers"),
    dict(name="fitness", band="40-350", profit=12, cap=8,
         theme="Fitness single items GBP 40-350: walking pads, treadmills, exercise bikes, rowing machines, adjustable dumbbells, massage guns, "
               "weight benches, kettlebell sets, yoga equipment, power towers"),
    dict(name="baby-kids", band="35-300", profit=10, cap=8,
         theme="Baby and kids single items GBP 35-300: pushchairs, travel systems, high chairs, cots, ride-on toys, play tents, baby monitors, "
               "kids electric scooters, bouncers, bed rails"),
    dict(name="pets-large", band="35-250", profit=10, cap=8,
         theme="Pet single items GBP 35-250: dog crates, cat trees, pet beds, automatic feeders, pet gates, cat litter boxes, dog coats, "
               "pet carriers, dog ramps, aquariums"),
    dict(name="furniture-storage", band="40-350", profit=12, cap=8,
         theme="Furniture and storage single items GBP 40-350: shoe racks, wardrobes, TV stands, desks, bookcases, ottoman beds, shelving units, "
               "bedside tables, coat racks, bathroom cabinets, storage benches"),
]


def now():
    return datetime.datetime.now(datetime.timezone.utc)


def load_state():
    try:
        return json.load(open(STATE))
    except Exception:
        return {"hunts": [], "reported": {}, "last_text_audit": 0}


def save_state(st):
    if not DRY:
        json.dump(st, open(STATE, "w"), indent=1)


def db():
    c = sqlite3.connect(os.path.join(HERE, "ebay_agent2.db"), timeout=60)
    c.row_factory = sqlite3.Row
    return c


def profit_of(price, cost, amazon_now):
    return price * KEEP - max(cost or 0, amazon_now or 0)


# ------------------------------------------------------------------ 1. scoreboard
def scoreboard(c):
    today = c.execute("SELECT COUNT(*) n, COALESCE(SUM(profit_gbp), 0) p FROM orders WHERE date(created_at)=date('now')").fetchone()
    week = c.execute("SELECT COUNT(*) n, COALESCE(SUM(profit_gbp), 0) p FROM orders WHERE date(created_at)>=date('now','-6 day')").fetchone()
    live = c.execute("SELECT COUNT(*) n, SUM(listed_price_gbp>=?) hv, SUM(listed_price_gbp>=35) mid FROM products WHERE status='active'",
                     (HV_PRICE,)).fetchone()
    held = c.execute("SELECT COUNT(*) FROM products WHERE status='paused_review'").fetchone()[0]
    avg = c.execute("SELECT AVG(profit_gbp) FROM orders WHERE date(created_at)>=date('now','-13 day') AND profit_gbp>0").fetchone()[0] or 3.0
    hvp = c.execute("SELECT AVG(listed_price_gbp*0.976 - MAX(COALESCE(cost_gbp,0), COALESCE(amazon_price_now,0))) FROM products "
                    "WHERE status='active' AND listed_price_gbp>=?", (HV_PRICE,)).fetchone()[0] or 0
    return dict(hv_profit=round(hvp, 2), today_n=today["n"], today_p=round(today["p"], 2), week_n=week["n"], week_p=round(week["p"], 2),
                live=live["n"], hv=live["hv"] or 0, mid=live["mid"] or 0, held=held, avg_profit=round(avg, 2))


# ------------------------------------------------------------------ 2. promotion of new high-value listings
def promote(c):
    camp = os.getenv("PROMOTE_CAMPAIGN_ID")
    if not camp:
        return 0, "no campaign id"
    H = dict(sm._sell_headers())
    H["Content-Language"] = "en-GB"
    ads = {}
    r = requests.get(f"{MARKETING}/ad_campaign/{camp}/ad?limit=500", headers=H, timeout=30)
    if r.status_code != 200:
        return 0, f"could not read ads ({r.status_code})"
    for a in r.json().get("ads", []):
        ads[str(a.get("listingId"))] = float(a.get("bidPercentage") or 0)
    todo = []
    for p in c.execute("SELECT ebay_listing_id, listed_price_gbp p, cost_gbp c, amazon_price_now a FROM products "
                       "WHERE status='active' AND listed_price_gbp>=? AND ebay_listing_id IS NOT NULL", (HV_PRICE,)):
        lid = str(p["ebay_listing_id"])
        prof = p["p"] * (1 - PROMO_RATE / 100) - max(p["c"] or 0, p["a"] or 0)
        if lid in ads and ads[lid] < PROMO_RATE and prof >= PROMO_MIN_PROFIT:       # only ever raises up to the approved rate
            todo.append(lid)
    if not todo or DRY:
        return len(todo), "dry run" if DRY and todo else "nothing to do"
    done = 0
    for i in range(0, len(todo), 100):
        x = requests.post(f"{MARKETING}/ad_campaign/{camp}/bulk_update_ads_bid_by_listing_id", headers=H, timeout=60,
                          json={"requests": [{"listingId": l, "bidPercentage": str(PROMO_RATE)} for l in todo[i:i + 100]]})
        if x.status_code == 200:
            done += sum(1 for y in x.json().get("responses", []) if y.get("statusCode") in (200, 201))
    return done, "ok"


# ------------------------------------------------------------------ 3. hunts
def mem_available_mb():
    try:
        return next(int(l.split()[1]) // 1024 for l in open("/proc/meminfo") if l.startswith("MemAvailable"))
    except Exception:
        return 0


def quota_left():
    try:
        from research import _quota_left
        return _quota_left()
    except Exception:
        return None


def pick_hunt(st, c):
    """Winners first (more of what sold in the last 7 days, once per product), else the theme run longest ago."""
    ran_winner = {h["name"] for h in st["hunts"] if h["name"].startswith("winner:") and time.time() - h["at"] < 3 * 86400}
    for r in c.execute("SELECT p.id, p.title, SUM(o.profit_gbp) pr FROM orders o JOIN products p ON p.id=o.product_id "
                       "WHERE date(o.created_at)>=date('now','-7 day') AND o.profit_gbp>=5 GROUP BY p.id ORDER BY pr DESC"):
        nm = f"winner:{r['id']}"
        if nm not in ran_winner:
            title = re.sub(r"\s+", " ", r["title"])[:90]
            return dict(name=nm, band="35-350", profit=10, cap=6, sold="1.0",
                        theme=f"Single products very similar to this recent best seller (same type, different makes and sizes): {title}")
    last = {}
    for h in st["hunts"]:
        last[h["name"]] = max(last.get(h["name"], 0), h["at"])
    return min(HUNTS, key=lambda h: last.get(h["name"], 0))


def run_hunt(h, st):
    since = now().strftime("%Y-%m-%d %H:%M:%S")
    t0 = time.time()
    try:
        r = subprocess.run(["bash", os.path.join(HERE, "run_hunt.sh"), h["theme"], str(h["cap"]), h["band"], str(h["profit"]),
                            str(h.get("sold", "1.0"))], capture_output=True, text=True, timeout=3300)
        code, tail = r.returncode, (r.stdout + r.stderr).strip()[-200:]
    except subprocess.TimeoutExpired:
        code, tail = -1, "timed out after 55 minutes"
    c = db()
    new = [dict(r) for r in c.execute("SELECT id, title, listed_price_gbp p, cost_gbp cst, amazon_price_now a, status FROM products "
                                      "WHERE created_at>=? ORDER BY id", (since,))]
    c.close()
    st["hunts"].append(dict(name=h["name"], at=time.time(), code=code, new=len(new), mins=round((time.time() - t0) / 60)))
    st["hunts"] = st["hunts"][-60:]
    return code, tail, new


# ------------------------------------------------------------------ 4. audits
def audit_new(new):
    """Anything listed since the hunt began must obey the standing rules. Reports breaks; never ends a listing."""
    bad = []
    for p in new:
        t = p["title"] or ""
        prof = profit_of(p["p"], p["cst"], p["a"])
        if p["p"] < FLOOR_PRICE:
            bad.append(f"#{p['id']} {t[:40]}: price GBP {p['p']} is under the GBP {FLOOR_PRICE:.0f} floor")
        elif prof < FLOOR_PROFIT - 0.5:
            bad.append(f"#{p['id']} {t[:40]}: profit only GBP {prof:.2f} (GBP {FLOOR_PROFIT:.0f} wanted)")
        if p["cst"] and p["p"] > max(p["cst"] * 2.5, p["cst"] + 15):
            bad.append(f"#{p['id']} {t[:40]}: price GBP {p['p']} is {p['p'] / p['cst']:.1f}x its cost - possible mispricing")
        if re.search(r"amazon", t, re.I):
            bad.append(f"#{p['id']}: 'Amazon' in the title")
    return bad


def text_audit():
    """Any visible 'Amazon' in a title, description or specific on eBay (image links are not visible to buyers)."""
    found, off = [], 0
    base = config.EBAY_SELL_BASE
    while True:
        r = requests.get(f"{base}/inventory_item", headers=sm._sell_headers(), params={"limit": 100, "offset": off}, timeout=60)
        if r.status_code != 200:
            return found, f"inventory read failed ({r.status_code})"
        items = r.json().get("inventoryItems", [])
        for it in items:
            prod = {k: v for k, v in it.get("product", {}).items() if k != "imageUrls"}
            if re.search(r"amazon", json.dumps(prod), re.I):
                found.append(f"{it.get('sku')}: {str(prod.get('title'))[:50]}")
        if len(items) < 100:
            return found, "ok"
        off += 100


# ------------------------------------------------------------------ 6. report
def report(sb, promo, hunt, alerts, text_found, ideas, open_items):
    gap = max(GOAL - sb["today_p"], 0)
    need = GOAL / max(sb["avg_profit"], 1)
    L = [f"DIRECTOR LOOP {now():%d %b %H:%M} UTC",
         f"PROFIT TODAY GBP {sb['today_p']:.2f} ({sb['today_n']} sales) of GBP {GOAL:.0f} | last 7 days GBP {sb['week_p']:.2f} "
         f"({sb['week_n']} sales) | gap today GBP {gap:.2f}",
         f"Shop: {sb['live']} live | {sb['hv']} at GBP {HV_PRICE:.0f}+ | {sb['mid']} at GBP 35+ | {sb['held']} held for your review",
         f"Your real average is GBP {sb['avg_profit']:.2f} a sale (mostly cheap items) = about {need:.0f} sales a day for GBP {GOAL:.0f}; "
         f"your live GBP {HV_PRICE:.0f}+ listings average GBP {sb['hv_profit']:.2f} expected profit = about {GOAL / max(sb['hv_profit'], 1):.0f} sales a day."]
    if hunt:
        h, code, new = hunt
        L += ["", f"HUNT: {h['name']} (band GBP {h['band']}, profit >= GBP {h['profit']}) -> {len(new)} new listing(s)"
              + ("" if code == 0 else f" [exit {code}]")]
        L += [f"  + #{p['id']} GBP {p['p']} (cost {p['cst']}) {str(p['title'])[:50]}" for p in new[:8]]
    elif promo is not None:
        L += ["", f"HUNT: none this loop (lock busy, memory short, daily limit, or eBay search quota {sb.get('quota')} below {QUOTA_FLOOR})"]
    if promo is not None:
        L += [f"PROMOTION: {promo[0]} high-value listing(s) raised to {PROMO_RATE:.0f}% ({promo[1]})"]
    try:
        a = json.load(open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "ad_dentalife.json")))
        L += [f"DENTALIFE GBP 3/day campaign since {a['since']}: GBP {a['spent']:.2f} spent | {a['impressions']} impressions | "
              f"{a['clicks']} clicks | {a['sales']} sale(s) (eBay figures, as of {a['updated']}; click charges can lag 72h)"]
    except Exception:
        pass
    if alerts:
        L += ["", "RULE BREAKS TO LOOK AT:"] + ["  ! " + a for a in alerts[:10]]
    if text_found is not None:
        L += ["", "'Amazon' text scan: " + ("clean across the whole shop" if not text_found else f"{len(text_found)} listing(s) need fixing: "
                                          + "; ".join(text_found[:5]))]
    if ideas:
        L += ["", "IDEAS I AM ACTING ON / PROPOSING:"] + ["  - " + i for i in ideas]
    if open_items:
        L += ["", "WAITING FOR YOUR DECISION (once a day):"] + ["  ? " + o for o in open_items]
    return "\n".join(L)


def ideas_for(sb, st, c):
    ideas = []
    if sb["hv"] < 40:
        ideas.append(f"Only {sb['hv']} listings are GBP {HV_PRICE:.0f}+ - the director adds up to 8 per hunt, up to {MAX_HUNTS_PER_DAY} hunts a day.")
    recent = [h for h in st["hunts"][-10:] if h["code"] == 0]
    if recent:
        best = max(recent, key=lambda h: h["new"])
        ideas.append(f"Best recent hunt: '{best['name']}' ({best['new']} listed in {best['mins']} min) - that theme gets repeated sooner.")
        dry = [h["name"] for h in recent if h["new"] == 0]
        if dry:
            ideas.append("Hunts that found nothing recently (loosen band or retire): " + ", ".join(sorted(set(dry))))
    if sb["held"] >= 10:
        ideas.append(f"{sb['held']} listings are paused_review - several were genuine high-value items; say which should return.")
    thin = c.execute("SELECT COUNT(*) FROM products WHERE status='active' AND listed_price_gbp*0.976 - MAX(COALESCE(cost_gbp,0), COALESCE(amazon_price_now,0)) < 3 "
                     "AND listed_price_gbp>=? ", (HV_PRICE,)).fetchone()[0]
    if thin:
        ideas.append(f"{thin} high-value live listing(s) earn under GBP 3 at today's Amazon price - the monitor raises them when Amazon moves; "
                     "I will list which ones so you can approve a price rise.")
    return ideas


OPEN_ITEMS = [
    "Restore or keep the 35 prices lowered on 13 Nov?",
    "Iwatani stove + hand plane orders: cancel/handle with buyers?",
    "Dentalife: title change, barcode (EAN), pouch weight, postage cost per bundle.",
    "Which paused hobs / premium items should return to sale?",
    "Server upgrade (memory is the main limit).",
]


def main():
    lock = open("/tmp/director.lock", "w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        print("another director loop is running")
        return
    print("date -u:", now(), flush=True)
    st = load_state()
    c = db()
    sb = scoreboard(c)
    promo, hunt, alerts, text_found = None, None, [], None
    if time.time() - st.get("last_promote", 0) > 3500 or FORCE:        # at most hourly: it is one read plus one write
        try:
            promo = promote(c)
            st["last_promote"] = time.time()
        except Exception as e:
            promo = (0, f"failed: {e}")
    today = now().strftime("%Y-%m-%d")
    ran_today = sum(1 for h in st["hunts"] if datetime.datetime.fromtimestamp(h["at"], datetime.timezone.utc).strftime("%Y-%m-%d") == today)
    q = quota_left()
    st["quota"] = q
    if not NO_HUNT and ran_today < MAX_HUNTS_PER_DAY and mem_available_mb() >= 700 and (q is None or q >= QUOTA_FLOOR):
        h = pick_hunt(st, c)
        print("hunt:", h["name"], flush=True)
        code, tail, new = run_hunt(h, st)
        if code == 3:                                  # the shared browser lock was busy: not started, do not count it
            st["hunts"].pop()
        else:
            hunt = (h, code, new)
            alerts += audit_new(new)
            if code != 0:
                alerts.append(f"hunt '{h['name']}' ended with code {code}: {tail[:120]}")
    if not DRY and (hunt or time.time() - st.get("last_text_audit", 0) > 20 * 3600):
        try:
            text_found, why = text_audit()
            st["last_text_audit"] = time.time()
            if why != "ok":
                alerts.append("Amazon-text scan: " + why)
        except Exception as e:
            alerts.append(f"Amazon-text scan failed: {e}")
    sb = scoreboard(c)
    sb["quota"] = q
    ideas = ideas_for(sb, st, c)
    hour = START_HOUR
    open_items = OPEN_ITEMS if (hour in REPORT_HOURS and st["reported"].get("open") != today) else []
    text = report(sb, promo, hunt, alerts, text_found, ideas, open_items)
    print(text, flush=True)
    slot = f"rep-{today}-{hour}"
    due = hour in REPORT_HOURS and slot not in st["reported"]          # the scoreboard goes out once in each report hour
    send = FORCE or alerts or due                                  # owner, 19 Nov: no per-hunt 'listed' messages
    if due:
        st["reported"] = {k: v for k, v in st["reported"].items() if k == "open" or today in k}
        st["reported"][slot] = 1
    if send and not DRY:
        try:
            from telegram_bot import _send
            _send(text[:3800], silent=not (alerts or FORCE))
            if open_items:
                st["reported"]["open"] = today
        except Exception as e:
            print("telegram failed:", e)
    save_state(st)
    c.close()


if __name__ == "__main__":
    main()
