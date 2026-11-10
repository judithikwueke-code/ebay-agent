#!/usr/bin/env python3
"""
MARKET RADAR - the agent's intelligence layer.

Instead of guessing keywords, it watches what is ACTUALLY selling on eBay UK:
  1. SWEEP   every category cell: eBay's best-match top 200, the best new listings (last 21 days), most-watched items,
             and the other listings of sellers who already own fast movers.
  2. SNAPSHOT each promising listing's sold counter, then again hours later. Sold difference / selling days = a
             MEASURED sales rate (not a lifetime average).
  3. CLASSIFY ROCKET (new and >=2/day), FAST (>=2/day), RISING (speeding up), STEADY (>=1/day), HIGH (>=GBP 60, sells
             every other day), or PROV (one snapshot only - lifetime average, still to be confirmed).
  4. PUSH    the winners to the agent's watch queue (watch_items); watch_scan.py finds the product on Amazon, checks
             the profit and lists it. Results flow back and steer the next sweeps.

    python market_radar.py run [--budget 500] [--digest] [--no-push] [--cells N]
    python market_radar.py report [--top 15] | digest | push [--cap N] | cells
"""
import argparse
import logging
import math
import os
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
os.chdir(HERE)
sys.path.insert(0, HERE)
from dotenv import load_dotenv

load_dotenv(os.path.join(HERE, ".env"))
import requests

import radar_db as R
from config import EBAY_APP_ID, EBAY_BROWSE_BASE, get_app_token

log = logging.getLogger("radar")
RUN_BUDGET = int(os.getenv("RADAR_RUN_BUDGET", "500"))
QUOTA_FLOOR = int(os.getenv("RADAR_QUOTA_FLOOR", "900"))       # never take the shared daily quota below this
WORKERS = int(os.getenv("RADAR_WORKERS", "3"))
MAX_TRACKING = int(os.getenv("RADAR_MAX_TRACKING", "700"))

_tl = threading.local()
_lock = threading.Lock()
_calls = 0
_watch_fail = 0
_stop = threading.Event()


def calls() -> int:
    return _calls


def _hdr():
    with _lock:
        tok = get_app_token()
    return {"Authorization": "Bearer " + tok, "X-EBAY-C-MARKETPLACE-ID": "EBAY_GB"}


def _sess():
    if getattr(_tl, "s", None) is None:
        _tl.s = requests.Session()
    return _tl.s


def _get(path, params=None, timeout=40):
    """One Browse call -> (status, json). Every HTTP request is counted. A second 429 stops the whole run."""
    global _calls
    for attempt in (0, 1):
        if _stop.is_set():
            return 0, None
        with _lock:
            _calls += 1
        try:
            r = _sess().get(EBAY_BROWSE_BASE + path, headers=_hdr(), params=params, timeout=timeout)
        except requests.RequestException:
            if attempt == 0:
                time.sleep(2)
                continue
            return -1, None
        if r.status_code == 429:
            if attempt == 0:
                time.sleep(20)
                continue
            _stop.set()
            log.warning("eBay rate limit hit twice - stopping this run")
            return 429, None
        if r.status_code >= 500 and attempt == 0:
            time.sleep(2)
            continue
        try:
            return r.status_code, r.json()
        except ValueError:
            return r.status_code, None
    return -1, None


def quota_left():
    try:
        from research import _quota_left
        return _quota_left()
    except Exception as e:
        log.warning(f"quota check failed: {e}")
        return None


# ---------------------------------------------------------------- sweeps
def _flt(band, new=False, seller=None):
    lo, hi = (8, 500) if seller else R.BANDS[band]
    f = f"price:[{lo}..{hi}],priceCurrency:GBP,buyingOptions:{{FIXED_PRICE}},conditionIds:{{1000}}"
    if seller:
        return f + f",sellers:{{{seller}}}"
    f += ",itemLocationCountry:GB"
    if new:
        t = datetime.now(timezone.utc)
        since, until = ((t - timedelta(days=d)).strftime("%Y-%m-%dT%H:%M:%SZ") for d in (R.NEW_DAYS, R.NEW_MIN_DAYS))
        f += f",itemStartDate:[{since}..{until}]"
    return f


def _sweep_fetch(job):
    """job = (cell dict, src). Returns (job, list of raw items)."""
    global _watch_fail
    cell, src = job
    if src == "watch":                                           # Merchandising API: separate quota, not counted
        if _watch_fail >= 15 or _stop.is_set():                  # that API is refusing us: leave it for the next run
            return job, None
        try:
            r = _sess().get("https://svcs.ebay.com/MerchandisingService", timeout=25, params={
                "OPERATION-NAME": "getMostWatchedItems", "SERVICE-NAME": "MerchandisingService", "SERVICE-VERSION": "1.1.0",
                "CONSUMER-ID": EBAY_APP_ID, "RESPONSE-DATA-FORMAT": "JSON", "REST-PAYLOAD": "", "GLOBAL-ID": "EBAY-GB",
                "maxResults": 50, "categoryId": cell["cat_id"]})
            js = r.json().get("getMostWatchedItemsResponse", {})
            if isinstance(js, list):
                js = js[0] if js else {}
            if "itemRecommendations" not in js and "success" not in str(js.get("ack", "")).lower():
                raise ValueError("no data")
            _watch_fail = 0
            return job, (js.get("itemRecommendations") or {}).get("item", []) or []
        except Exception:
            _watch_fail += 1
            return job, None
    if src == "seller":
        st, js = _get("/item_summary/search", {"category_ids": cell["root"], "limit": 100,
                                                "filter": _flt("A", seller=cell["seller"])})
    else:
        st, js = _get("/item_summary/search", {"category_ids": cell["cat_id"], "limit": 200,
                                                "filter": _flt(cell["band"], new=(src == "new"))})
    return job, ((js or {}).get("itemSummaries", []) if st == 200 else None)


def _parse_summary(s):
    iid = s.get("itemId") or ""
    key = s.get("legacyItemId") or (iid.split("|")[1] if iid.count("|") >= 2 else "")
    try:
        price = float(s["price"]["value"])
    except Exception:
        return None
    if not key or (s.get("price") or {}).get("currency", "GBP") != "GBP":
        return None
    ship = 0.0
    try:
        ship = float(((s.get("shippingOptions") or [{}])[0].get("shippingCost") or {}).get("value") or 0)
    except Exception:
        pass
    sel = s.get("seller") or {}
    return {"key": str(key), "item_id": iid, "is_group": 1 if s.get("itemGroupType") else 0, "title": s.get("title", ""),
            "price": price, "ship": ship, "seller": sel.get("username") or "", "seller_fb": sel.get("feedbackScore"),
            "image": (s.get("image") or {}).get("imageUrl", ""), "created": s.get("itemCreationDate"),
            "epid": s.get("epid"), "promoted": 1 if s.get("priorityListing") else 0, "watch_count": None}


def _parse_watched(m):
    try:
        price = float((m.get("buyItNowPrice") or m.get("currentPrice") or {}).get("__value__"))
        ship = float((m.get("shippingCost") or {}).get("__value__") or 0)
    except Exception:
        return None
    if m.get("country") != "GB" or not m.get("itemId"):
        return None
    return {"key": str(m["itemId"]), "item_id": "", "is_group": 0, "title": m.get("title", ""), "price": price,
            "ship": ship, "seller": "", "seller_fb": None, "image": m.get("imageURL", ""), "created": None, "epid": None,
            "promoted": None, "watch_count": int(m.get("watchCount") or 0)}


def _prio(ranks, watch_count, weight, promoted, srcs, age):
    """How soon a listing deserves its first snapshot (the scarce resource): 0..135."""
    p = 0.0
    if ranks["best"] is not None:
        p = max(p, 100 / (1 + ranks["best"] / 5))
    if ranks["new"] is not None:
        p = max(p, 90 / (1 + ranks["new"] / 4))
    if ranks["seller"] is not None:
        p = max(p, 75 / (1 + ranks["seller"] / 4))
    if age is not None and age < R.NEW_MIN_DAYS:
        p *= 0.4                                                 # just listed: its rank is eBay's new-listing boost
    if ranks["watch"] is not None:                               # watchers are the hardest signal before a snapshot
        p = max(p, min(100.0, 22 * math.log10((watch_count or 0) + 1) + 12 / (1 + ranks["watch"] / 4)))
        if len(srcs) >= 2:
            p *= 1.35                                            # most-watched AND ranking high: two independent signals
    return round(p * weight * (0.6 if promoted else 1.0), 2)


def ingest(c, d, cat_id, band, root, weight, src, rank, now) -> int:
    """Insert or refresh one listing seen in a sweep. Returns 1 if it is new to the radar."""
    row = c.execute("SELECT sources,best_rank,new_rank,watch_rank,seller_rank,watch_count,status,last_snap,promoted,created "
                    "FROM items WHERE key=?", (d["key"],)).fetchone()
    ranks = {"best": None, "new": None, "watch": None, "seller": None}
    srcs, wc, promoted, created = set(), d.get("watch_count"), d.get("promoted"), d.get("created")
    if row:
        ranks = {"best": row["best_rank"], "new": row["new_rank"], "watch": row["watch_rank"], "seller": row["seller_rank"]}
        srcs = set(filter(None, (row["sources"] or "").split(",")))
        wc = wc if wc is not None else row["watch_count"]
        promoted = row["promoted"] if promoted is None else promoted
        created = created or row["created"]
    ranks[src] = rank
    srcs.add(src)
    prio = _prio(ranks, wc, weight, promoted, srcs, R.age_days(created, now))
    url = f"https://www.ebay.co.uk/itm/{d['key']}"
    if row is None:
        c.execute(
            "INSERT INTO items (key,item_id,is_group,title,price,ship,seller,seller_fb,root,cell,cat_id,image,url,created,"
            "epid,sources,best_rank,new_rank,watch_rank,seller_rank,watch_count,promoted,prio,first_seen,last_seen,status) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,'queued')",
            (d["key"], d["item_id"], d["is_group"], d["title"], d["price"], d["ship"], d["seller"], d["seller_fb"], root,
             f"{cat_id}:{band}", cat_id, d["image"], url, d["created"], d["epid"], ",".join(sorted(srcs)), ranks["best"],
             ranks["new"], ranks["watch"], ranks["seller"], wc, promoted or 0, prio, now, now))
        return 1
    status = row["status"]
    if status == "cold" and rank < 15 and (row["last_snap"] or 0) < now - 10 * 86400:
        status = "queued"                                        # still ranking high 10 days later: measure again
    c.execute(
        "UPDATE items SET sources=?,best_rank=?,new_rank=?,watch_rank=?,seller_rank=?,watch_count=?,promoted=?,prio=?,"
        "last_seen=?,title=COALESCE(NULLIF(?,''),title),price=?,ship=?,item_id=COALESCE(NULLIF(?,''),item_id),"
        "seller=COALESCE(NULLIF(?,''),seller),status=? WHERE key=?",
        (",".join(sorted(srcs)), ranks["best"], ranks["new"], ranks["watch"], ranks["seller"], wc, promoted or 0, prio, now,
         d["title"], d["price"], d["ship"], d["item_id"], d["seller"], status, d["key"]))
    return 0


def do_sweeps(c, call_cap, max_cells=None) -> dict:
    now = time.time()
    every = [dict(x) for x in c.execute("SELECT * FROM cells")]
    cells = [x for x in every if x["band"] != "W"]
    wcells = sorted((x for x in every if x["band"] == "W"), key=lambda x: -x["weight"])

    def score(x):
        return x["weight"] * (1 + 2 * (x["fast"] + 3 * x["viable"]) / (x["snapped"] + 8))
    cells.sort(key=lambda x: (1 if x["last_best"] else 0, -(x["weight"] if not x["last_best"] else score(x))))
    if max_cells:
        cells, wcells = cells[:max_cells], wcells[:max_cells * 3]
    paid, free, seen_cat = [], [], set()
    for x in cells:
        if not x["last_best"] or now - x["last_best"] > R.BEST_EVERY_H * 3600:
            paid.append((x, "best"))
        if not x["last_new"] or now - x["last_new"] > R.NEW_EVERY_H * 3600:
            paid.append((x, "new"))
    for x in cells + wcells:                                     # most-watched lists cost nothing: every category
        if x["cat_id"] not in seen_cat and (not x["last_watched"] or now - x["last_watched"] > R.WATCH_EVERY_H * 3600):
            seen_cat.add(x["cat_id"])
            free.append((x, "watch"))
    sellers = [] if max_cells else [dict(s) for s in c.execute(
        "SELECT * FROM sellers WHERE fast + 4*viable >= 2 AND (last_sweep IS NULL OR last_sweep < ?) AND seller != '' "
        "ORDER BY fast + 4*viable DESC LIMIT 12", (now - 3 * 86400,))]
    paid = [({"seller": s["seller"], "root": s["root"], "cat_id": s["root"], "band": "A",
              "weight": R.ROOTS.get(s["root"], ("", 0.6))[1]}, "seller") for s in sellers if s["root"]] + paid
    paid = paid[:max(call_cap, 0)]
    stat = {"calls": 0, "new_items": 0, "sweeps": 0, "failed": 0}
    trend = {"new": {}, "best": {}}
    start = calls()
    with ThreadPoolExecutor(WORKERS) as ex:
        for job, raw in ex.map(_sweep_fetch, free + paid):
            cell, src = job
            if raw is None:
                stat["failed"] += 1
                continue
            stat["sweeps"] += 1
            stored, seen_pairs = 0, set()
            for rank, s in enumerate(raw):
                d = _parse_watched(s) if src == "watch" else _parse_summary(s)
                if not d or d["seller"].lower() == R.OWN_SELLER or R.title_blocked(d["title"]):
                    continue
                if src in trend:                                 # trend terms: count distinct sellers per word pair
                    for bg in R.bigrams(d["title"]):
                        if (bg, d["seller"]) not in seen_pairs:
                            seen_pairs.add((bg, d["seller"]))
                            trend[src][(cell["root"], bg)] = trend[src].get((cell["root"], bg), 0) + 1
                if stored >= R.STORE_TOP[src]:
                    continue
                band = cell["band"]
                if src in ("watch", "seller"):
                    band = next((b for b, (lo, hi) in R.BANDS.items() if lo <= d["price"] < hi), None)
                    if not band:
                        continue
                home = cell["parent"] if cell["band"] == "W" else cell["cat_id"]     # sub-category results count
                stat["new_items"] += ingest(c, d, home, band, cell["root"], cell["weight"], src, rank, now)  # for the parent
                stored += 1
            if src == "seller":
                c.execute("UPDATE sellers SET last_sweep=? WHERE seller=?", (now, cell["seller"]))
            else:
                col = {"best": "last_best", "new": "last_new", "watch": "last_watched"}[src]
                c.execute(f"UPDATE cells SET {col}=? WHERE cat_id=?" + ("" if src == "watch" else " AND band=?"),
                          (now, cell["cat_id"]) if src == "watch" else (now, cell["cat_id"], cell["band"]))
            c.commit()
    day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    for src, col in (("new", "n_new"), ("best", "n_best")):
        rows = [(day, root, term, n) for (root, term), n in trend[src].items() if n >= 3]
        c.executemany(f"INSERT INTO terms (day, root, term, {col}) VALUES (?,?,?,?) ON CONFLICT(day, root, term) "
                      f"DO UPDATE SET {col} = {col} + excluded.{col}", rows)
    c.execute("DELETE FROM terms WHERE day < ?", ((datetime.now(timezone.utc) - timedelta(days=30)).strftime("%Y-%m-%d"),))
    c.execute("DELETE FROM items WHERE status='queued' AND snaps=0 AND last_seen < ?", (now - 4 * 86400,))
    c.commit()
    stat["calls"] = calls() - start
    return stat


# ---------------------------------------------------------------- snapshots
def _one(it):
    av = (it.get("estimatedAvailabilities") or [{}])[0]
    qty = av.get("estimatedAvailableQuantity")                   # None = "more than 10 available" (number not given)
    if av.get("estimatedAvailabilityStatus") == "OUT_OF_STOCK":
        qty = 0
    ship = None
    try:
        price = float((it.get("price") or {}).get("value") or 0) or None
        so = it.get("shippingOptions") or []
        if so:
            ship = float((so[0].get("shippingCost") or {}).get("value") or 0)
    except Exception:
        price = None
    sel = it.get("seller") or {}
    return {"sold": int(av.get("estimatedSoldQuantity") or 0), "avail": qty, "price": price, "ship": ship,
            "created": it.get("itemCreationDate"), "brand": it.get("brand"), "mpn": it.get("mpn"), "gtin": it.get("gtin"),
            "epid": it.get("epid"), "cat": it.get("categoryId"), "title": it.get("title"), "item_id": it.get("itemId"),
            "seller": sel.get("username"), "seller_fb": sel.get("feedbackScore"),
            "image": (it.get("image") or {}).get("imageUrl"), "cond": it.get("conditionId"),
            "auction": bool(it.get("buyingOptions")) and "FIXED_PRICE" not in it["buyingOptions"]}


def _snap_fetch(job):
    """One listing's current sold counter. job: key, item_id, is_group, snaps."""
    key = job["key"]

    def group():
        st, js = _get("/item/get_items_by_item_group", {"item_group_id": key}, timeout=60)
        if st == 404:
            return {"key": key, "st": "gone"}
        its = (js or {}).get("items") or []
        if st != 200 or not its:
            return {"key": key, "st": "err", "code": st}
        vs = [_one(x) for x in its]
        top = max(vs, key=lambda v: v["sold"])
        made = [v["created"] for v in vs if v["created"]]
        return {**top, "key": key, "st": "ok", "is_group": 1, "sold": sum(v["sold"] for v in vs),
                "avail": None if any(v["avail"] is None for v in vs) else sum(v["avail"] for v in vs),
                "created": min(made) if made else None}

    if job["is_group"]:
        return group()
    if job["item_id"]:
        params = {"fieldgroups": "COMPACT"} if job["snaps"] else None
        st, js = _get("/item/" + requests.utils.quote(job["item_id"], safe=""), params)
    else:
        st, js = _get("/item/get_item_by_legacy_id", {"legacy_item_id": key})
        if st == 400 and any(e.get("errorId") == 11006 for e in (js or {}).get("errors", [])):
            return group()
    if st == 404:
        return {"key": key, "st": "gone"}
    if st != 200 or not js:
        return {"key": key, "st": "err", "code": st}
    return {**_one(js), "key": key, "st": "ok", "is_group": 0}


def apply_snap(c, r, now, tracking_full=False) -> str:
    row = c.execute("SELECT * FROM items WHERE key=?", (r["key"],)).fetchone()
    if row is None:
        return "?"
    if r["st"] == "gone":
        c.execute("UPDATE items SET status='gone', next_snap=NULL WHERE key=?", (r["key"],))
        return "gone"
    if r["st"] == "err":
        c.execute("UPDATE items SET prio=prio*0.5, next_snap=?, note=? WHERE key=?",
                  (now + 6 * 3600, f"snapshot failed ({r.get('code')})", r["key"]))
        return "err"
    if (r.get("cond") and str(r["cond"]) != "1000") or r.get("auction"):
        c.execute("UPDATE items SET status='cold', next_snap=NULL, snaps=snaps+1, last_snap=?, note=? WHERE key=?",
                  (now, "not a new fixed-price item", r["key"]))
        return "skip"
    sold = r["sold"]
    price = r["price"] or row["price"] or 0.0
    ship = r["ship"] if r["ship"] is not None else (row["ship"] or 0.0)
    total = price + ship
    created = r.get("created") or row["created"]
    age = R.age_days(created, now)
    life = round(sold / max(age if age is not None else 60.0, 1.0), 3)
    base = None                                                  # earlier snapshot to measure against
    for s in c.execute("SELECT ts, sold FROM snaps WHERE key=? ORDER BY ts", (r["key"],)):
        if now - R.WINDOW_H * 3600 <= s["ts"] <= now - R.MIN_GAP_H * 3600:
            base = s
            break
        if s["ts"] < now - R.WINDOW_H * 3600:
            base = s                                             # latest one older than the window (kept if none inside)
    c.execute("INSERT INTO snaps (key, ts, sold, avail, price) VALUES (?,?,?,?,?)", (r["key"], now, sold, r["avail"], price))
    n = (row["snaps"] or 0) + 1
    first_snap = row["first_snap"] or now
    top = (row["best_rank"] is not None and row["best_rank"] < 30) or row["watch_rank"] is not None or \
          (row["new_rank"] is not None and row["new_rank"] < 20)
    rate, d_sold, win, cls, accel = row["rate"], row["d_sold"], row["win_days"], row["cls"], row["accel"]
    if base is None:
        if n == 1 or cls in (None, "PROV"):
            cls = R.provisional(age, sold, life, total, top)
        track = bool(cls) or life >= 0.7 or (sold >= 10 and age is not None and age <= 30) or \
            (row["watch_count"] or 0) >= 800 or (total >= 60 and life >= 0.2)
        if tracking_full and not cls:
            track = False
        status, nxt = ("tracking", now + 5.5 * 3600) if track else ("cold", None)   # runs are 6 h apart
    else:
        win = R.eff_days(base["ts"], now)
        d_sold = sold - base["sold"]
        if d_sold < 0 or win < 0.1:
            rate, status, nxt = None, "tracking", now + 11.5 * 3600
            cls = cls if cls == "PROV" else None
        else:
            rate = round(d_sold / win, 3)
            accel = round(rate / max(life, 0.05), 2)
            measured = R.eff_days(first_snap, now)
            new_cls = R.classify(age, d_sold, rate, life, total)
            if new_cls == "SLOW" and measured < 1.0:             # too short a window to call it slow yet
                cls = cls if cls == "PROV" else None
                status, nxt = "tracking", now + 11.5 * 3600
            elif new_cls == "SLOW":
                cls, status, nxt = "SLOW", "cold", None
            else:
                cls, status = new_cls, "tracking"
                nxt = now + (21.5 if cls in ("ROCKET", "FAST", "RISING") else 43.5) * 3600
    c.execute(
        "UPDATE items SET sold=?, avail=?, price=?, ship=?, created=?, snaps=?, first_snap=?, last_snap=?, next_snap=?,"
        "life_rate=?, rate_prev=rate, rate=?, d_sold=?, win_days=?, accel=?, cls=?, status=?, is_group=?,"
        "item_id=COALESCE(?, item_id), brand=COALESCE(?, brand), mpn=COALESCE(?, mpn), gtin=COALESCE(?, gtin),"
        "epid=COALESCE(?, epid), cat_id=COALESCE(?, cat_id), seller=COALESCE(NULLIF(?, ''), seller),"
        "seller_fb=COALESCE(?, seller_fb), image=COALESCE(NULLIF(image, ''), ?), title=COALESCE(NULLIF(title, ''), ?) "
        "WHERE key=?",
        (sold, r["avail"], price, ship, created, n, first_snap, now, nxt, life, rate, d_sold, win, accel, cls, status,
         r.get("is_group", 0), r.get("item_id"), r.get("brand"), r.get("mpn"), r.get("gtin"), r.get("epid"), r.get("cat"),
         r.get("seller") or "", r.get("seller_fb"), r.get("image"), r.get("title"), r["key"]))
    return cls or status


def do_snaps(c, jobs, call_limit) -> dict:
    start, out = calls(), {}
    full = c.execute("SELECT COUNT(*) FROM items WHERE status='tracking'").fetchone()[0] >= MAX_TRACKING
    with ThreadPoolExecutor(WORKERS) as ex:
        for i in range(0, len(jobs), 45):
            room = call_limit - (calls() - start)
            if _stop.is_set() or room <= 0:
                break
            for r in ex.map(_snap_fetch, jobs[i:i + 45][:room]):
                res = apply_snap(c, r, time.time(), full)
                out[res] = out.get(res, 0) + 1
            c.commit()
    out["calls"] = calls() - start
    return out


# ---------------------------------------------------------------- run
def seed_cells(c):
    for cell in R.build_cells():
        c.execute("INSERT INTO cells (cat_id, band, name, root, depth, weight, parent) VALUES (?,?,?,?,?,?,?) "
                  "ON CONFLICT(cat_id, band) DO UPDATE SET weight=excluded.weight, name=excluded.name", cell)
    c.commit()


def run(budget, digest=False, push=True, max_cells=None):
    import radar_out as O
    c = R.connect()
    t0 = time.time()
    left = quota_left()
    budget = min(budget, 300) if left is None else max(0, min(budget, left - QUOTA_FLOOR))
    log.info(f"radar run: eBay calls left today {left}, this run may use {budget}")
    seed_cells(c)
    O.sync_outcomes(c)
    res = {"resnap": {}, "sweep": {}, "first": {}}
    if budget >= 10:
        cols = "key, item_id, is_group, snaps"
        due = [dict(x) for x in c.execute(
            f"SELECT {cols} FROM items WHERE status='tracking' AND next_snap IS NOT NULL AND next_snap <= ? "
            "ORDER BY (cls IS NOT NULL) DESC, COALESCE(rate, life_rate, 0) DESC LIMIT ?", (t0, int(budget * 0.6)))]
        res["resnap"] = do_snaps(c, due, int(budget * 0.6))
        bootstrap = c.execute("SELECT COUNT(*) FROM cells WHERE last_best IS NULL AND band != 'W'").fetchone()[0] > 0
        res["sweep"] = do_sweeps(c, min(int(budget * (0.45 if bootstrap else 0.30)), budget - calls()), max_cells)
        room = budget - calls()
        queue = [dict(x) for x in c.execute(
            f"SELECT {cols} FROM items WHERE status='queued' ORDER BY prio DESC LIMIT ?", (max(room, 0),))]
        res["first"] = do_snaps(c, queue, room)
    O.recompute(c)
    pushed = O.push(c) if push else {}
    c.execute("INSERT INTO runs (started, ended, calls, sweeps, first_snaps, resnaps, pushed, note) VALUES (?,?,?,?,?,?,?,?)",
              (t0, time.time(), calls(), res["sweep"].get("sweeps", 0), sum(v for k, v in res["first"].items() if k != "calls"),
               sum(v for k, v in res["resnap"].items() if k != "calls"), pushed.get("pushed", 0), str(res)[:900]))
    c.commit()
    log.info(f"radar run done in {time.time() - t0:.0f}s: {calls()} eBay calls | re-snapshots {res['resnap']} | "
             f"sweeps {res['sweep']} | first snapshots {res['first']} | push {pushed}")
    if digest:
        O.send_digest(c, calls(), quota_left())
    c.close()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s - %(message)s")
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["run", "report", "digest", "push", "cells"])
    ap.add_argument("--budget", type=int, default=RUN_BUDGET)
    ap.add_argument("--digest", action="store_true")
    ap.add_argument("--no-push", action="store_true")
    ap.add_argument("--cells", type=int, default=None)
    ap.add_argument("--top", type=int, default=15)
    ap.add_argument("--cap", type=int, default=None)
    a = ap.parse_args()
    if a.cmd == "run":
        run(a.budget, digest=a.digest, push=not a.no_push, max_cells=a.cells)
    elif a.cmd == "cells":
        cs = R.build_cells()
        for cell in cs:
            print(f"{cell[0]:>7} {cell[1]} w={cell[5]:<5} {R.ROOTS[cell[3]][0][:22]:<22} | {cell[2]}")
        print(len(cs), "cells:", sum(1 for x in cs if x[1] == "A"), "everyday (GBP 8-60),",
              sum(1 for x in cs if x[1] == "B"), "high-ticket (GBP 60-500),",
              sum(1 for x in cs if x[1] == "W"), "sub-categories (most-watched list only)")
    else:
        import radar_out as O
        conn = R.connect()
        if a.cmd == "push":
            O.sync_outcomes(conn)
            O.recompute(conn)
            print(O.push(conn, cap=a.cap))
        elif a.cmd == "digest":
            O.send_digest(conn, 0, quota_left())
        else:
            print(O.report(conn, top=a.top, limit=None))
