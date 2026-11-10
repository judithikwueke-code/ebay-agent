"""
Market radar outputs (see market_radar.py): push winners into the agent's watch queue, learn from what happened to
them, and report in plain English.
"""
import json
import math
import os
import sqlite3
import time
from datetime import datetime, timezone

import radar_db as R
from db import DB_PATH

AGENT_DB = DB_PATH if os.path.isabs(DB_PATH) else os.path.join(R.HERE, DB_PATH)
PUSH_CAP = int(os.getenv("RADAR_PUSH_CAP", "40"))            # new candidates handed to the agent per run
BACKLOG_CAP = int(os.getenv("RADAR_BACKLOG_CAP", "150"))     # never let more than this wait in the agent's queue
MAX_CELLS = int(os.getenv("RADAR_MAX_CELLS", "320"))
PUSH_MIN, PUSH_MAX = 9.0, 500.0
_FAST = "('ROCKET','FAST','RISING','STEADY','HIGH')"
_WATCH_SCHEMA = """CREATE TABLE IF NOT EXISTS watch_items (
    item_id TEXT PRIMARY KEY, url TEXT, title TEXT, price REAL, seller TEXT, seller_type TEXT, feedback INTEGER,
    sold_today INTEGER, sold_total INTEGER, in_baskets INTEGER, watchers INTEGER, available INTEGER,
    condition TEXT, brand TEXT, mpn TEXT, ean TEXT, model TEXT, images TEXT,
    captured_at TEXT, updated_at TEXT, status TEXT DEFAULT 'new', note TEXT, listing_id TEXT);"""


def _agent() -> sqlite3.Connection:
    a = sqlite3.connect(AGENT_DB, timeout=60)
    a.row_factory = sqlite3.Row
    a.execute(_WATCH_SCHEMA)
    for col, typ in (("source", "TEXT"), ("radar_rate", "REAL"), ("radar_cls", "TEXT"), ("radar_prio", "REAL")):
        try:
            a.execute(f"ALTER TABLE watch_items ADD COLUMN {col} {typ}")
        except sqlite3.OperationalError:
            pass                                                  # already there
    a.commit()
    return a


def _rate(it) -> float:
    """Measured rate when there is one, otherwise the lifetime average."""
    return (it["rate"] if it["cls"] != "PROV" and it["rate"] is not None else it["life_rate"]) or 0.0


def _push_prio(it) -> float:
    total = (it["price"] or 0) + (it["ship"] or 0)
    return round(R.CLS_W.get(it["cls"], 1.0) * math.sqrt(min(max(_rate(it), 0.0), 15.0)) * (0.6 + min(total, 60.0) / 60.0), 2)


def sync_outcomes(c):
    """Copy what the agent did with each pushed candidate (listed / no match / not viable ...) back into the radar."""
    a = _agent()
    rows = a.execute("SELECT item_id, status, note, listing_id FROM watch_items WHERE source='radar'").fetchall()
    a.close()
    for r in rows:
        c.execute("UPDATE items SET outcome=?, outcome_note=?, listing_id=? WHERE key=? AND pushed_at IS NOT NULL "
                  "AND COALESCE(outcome, '') != 'duplicate'", (r["status"], r["note"], r["listing_id"], r["item_id"]))
    c.commit()


def recompute(c):
    """Refresh per-category and per-seller scores, and open sub-categories of the ones that keep producing winners."""
    mine = "i.cell = cells.cat_id || ':' || cells.band"
    c.execute(f"""UPDATE cells SET
        seen = (SELECT COUNT(*) FROM items i WHERE {mine}),
        snapped = (SELECT COUNT(*) FROM items i WHERE {mine} AND i.snaps > 0),
        fast = (SELECT COUNT(*) FROM items i WHERE {mine} AND (i.cls IN {_FAST} OR i.cls = 'PROV')),
        viable = (SELECT COUNT(*) FROM items i WHERE {mine} AND i.outcome = 'listed')""")
    for r in c.execute(f"""SELECT seller, MAX(seller_fb) fb, COALESCE(SUM(cls IN {_FAST} OR cls = 'PROV'), 0) fast,
                                  COALESCE(SUM(outcome = 'listed'), 0) viable, COUNT(*) n, root
                           FROM items WHERE seller != '' AND snaps > 0 GROUP BY seller""").fetchall():
        c.execute("INSERT INTO sellers (seller, fb, fast, viable, items, root) VALUES (?,?,?,?,?,?) ON CONFLICT(seller) "
                  "DO UPDATE SET fb=excluded.fb, fast=excluded.fast, viable=excluded.viable, items=excluded.items, "
                  "root=excluded.root", (r["seller"], r["fb"], r["fast"], r["viable"], r["n"], r["root"]))
    total = c.execute("SELECT COUNT(*) FROM cells WHERE band != 'W'").fetchone()[0]   # only paid-sweep cells count
    for x in c.execute("SELECT * FROM cells WHERE fast >= 6 AND drilled = 0 AND depth <= 3 ORDER BY fast DESC").fetchall():
        try:
            kids = R.child_cells(x["cat_id"], x["band"], x["root"], x["weight"], x["depth"])
        except Exception:
            kids = []
        if total + len(kids) > MAX_CELLS:
            continue
        for k in kids:
            c.execute("INSERT OR IGNORE INTO cells (cat_id, band, name, root, depth, weight, parent) VALUES (?,?,?,?,?,?,?)", k)
        total += len(kids)
        c.execute("UPDATE cells SET drilled = 1 WHERE cat_id = ? AND band = ?", (x["cat_id"], x["band"]))
    c.commit()


def push(c, cap=None) -> dict:
    """Hand the best measured sellers to the agent (watch_items); refresh or withdraw the ones still waiting."""
    a = _agent()
    now = time.time()
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    out = {"pushed": 0, "refreshed": 0, "withdrawn": 0, "duplicates": 0}
    waiting = [r["item_id"] for r in a.execute("SELECT item_id FROM watch_items WHERE source='radar' AND status='new'")]
    for key in waiting:
        it = c.execute("SELECT * FROM items WHERE key=?", (key,)).fetchone()
        if not it:
            continue
        if it["status"] == "gone" or it["cls"] == "SLOW" or it["avail"] == 0:
            a.execute("UPDATE watch_items SET status='low_demand', note=?, updated_at=? WHERE item_id=? AND status='new'",
                      ("radar: measured slow, sold out or ended while waiting", stamp, key))
            out["withdrawn"] += 1
        elif it["cls"] in R.CLS_W:
            a.execute("UPDATE watch_items SET radar_rate=?, radar_cls=?, radar_prio=?, sold_total=?, price=?, updated_at=? "
                      "WHERE item_id=? AND status='new'", (round(_rate(it), 2), it["cls"], _push_prio(it), it["sold"],
                                                           round((it["price"] or 0) + (it["ship"] or 0), 2), stamp, key))
            out["refreshed"] += 1
    room = min(cap or PUSH_CAP, BACKLOG_CAP - (len(waiting) - out["withdrawn"]))
    if room > 0:
        scored = []
        for it in c.execute(f"SELECT * FROM items WHERE pushed_at IS NULL AND status='tracking' AND "
                            f"(cls IN {_FAST} OR cls='PROV') AND COALESCE(avail, 1) > 0").fetchall():
            total = (it["price"] or 0) + (it["ship"] or 0)
            if PUSH_MIN <= total <= PUSH_MAX and not R.title_blocked(it["title"] or ""):
                scored.append((_push_prio(it), it))
        scored.sort(key=lambda x: -x[0])
        recent = [set(R.tokens(r["title"])) for r in c.execute(
            "SELECT title FROM items WHERE pushed_at > ? AND COALESCE(outcome, '') != 'duplicate'", (now - 21 * 86400,))]
        for prio, it in scored:
            if out["pushed"] >= room:
                break
            tk = set(R.tokens(it["title"]))
            if tk and any(len(tk & o) / len(tk | o) >= 0.7 for o in recent if o):
                c.execute("UPDATE items SET pushed_at=?, outcome='duplicate', outcome_note='same product already queued' "
                          "WHERE key=?", (now, it["key"]))
                out["duplicates"] += 1
                continue
            rate, total = round(_rate(it), 2), round((it["price"] or 0) + (it["ship"] or 0), 2)
            how = (f"lifetime average {rate}/day ({it['sold']} sold), not yet confirmed" if it["cls"] == "PROV" else
                   f"measured {rate}/day (+{it['d_sold']} in {it['win_days']:.1f} selling days)")
            a.execute(
                "INSERT OR IGNORE INTO watch_items (item_id, url, title, price, seller, sold_total, watchers, available, brand, "
                "mpn, ean, images, captured_at, updated_at, status, note, source, radar_rate, radar_cls, radar_prio) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,'new',?,'radar',?,?,?)",
                (it["key"], it["url"], it["title"], total, it["seller"], it["sold"], it["watch_count"], it["avail"],
                 R.clean_brand(it["brand"]), it["mpn"], it["gtin"], json.dumps([it["image"]] if it["image"] else []),
                 stamp, stamp, f"radar {it['cls']}: {how}", rate, it["cls"], prio))
            c.execute("UPDATE items SET pushed_at=?, push_rate=? WHERE key=?", (now, rate, it["key"]))
            recent.append(tk)
            out["pushed"] += 1
    a.commit()
    a.close()
    c.commit()
    return out


# ---------------------------------------------------------------- reporting
def hot_types(c, n=8) -> list:
    """Product types where several different sellers each have a fast-moving listing."""
    agg = {}
    for it in c.execute(f"SELECT title, seller, cls, rate, life_rate FROM items WHERE cls IN {_FAST} OR cls='PROV'"):
        for bg in R.bigrams(it["title"] or ""):
            e = agg.setdefault(bg, [0, set(), 0.0])
            e[0] += 1
            e[1].add(it["seller"])
            e[2] += _rate(it)
    rows = [(bg, e[0], len(e[1]), round(e[2], 1)) for bg, e in agg.items() if e[0] >= 3 and len(e[1]) >= 2]
    rows.sort(key=lambda x: -x[3])
    out, used = [], set()
    for bg, cnt, sellers, rate in rows:                          # skip pairs that only repeat a word already shown
        w = set(bg.split())
        if w & used:
            continue
        used |= w
        out.append((bg, cnt, sellers, rate))
        if len(out) >= n:
            break
    return out


def emerging(c, n=8):
    """Word pairs that suddenly many more sellers use in new listings than on the days before (supply-side trend).
    Returns (rows, days_of_history); rows is None until there are 3 complete days to compare."""
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    days = [r[0] for r in c.execute("SELECT DISTINCT day FROM terms WHERE n_new > 0 AND day < ? ORDER BY day DESC LIMIT 8",
                                    (today,))]
    if len(days) < 3:
        return None, len(days)
    last, prev = days[0], days[1:]
    marks = ",".join("?" * len(prev))
    out = []
    for r in c.execute(f"""SELECT t.root, t.term, t.n_new, (SELECT COALESCE(SUM(p.n_new), 0) FROM terms p WHERE p.root = t.root
                           AND p.term = t.term AND p.day IN ({marks})) before FROM terms t
                           WHERE t.day = ? AND t.n_new >= 6 ORDER BY t.n_new DESC""", (*prev, last)):
        avg = r["before"] / len(prev)
        if r["n_new"] >= 2 * max(avg, 2.0):
            out.append((r["term"], r["n_new"], round(avg, 1), R.ROOTS.get(r["root"], ("?",))[0]))
        if len(out) >= n:
            break
    return out, len(days)


def funnel() -> dict:
    a = _agent()
    d = {r[0]: r[1] for r in a.execute("SELECT status, COUNT(*) FROM watch_items WHERE source='radar' GROUP BY status")}
    d["_listed"] = [dict(r) for r in a.execute("SELECT title, price, radar_rate, listing_id FROM watch_items WHERE "
                                               "source='radar' AND status='listed' ORDER BY updated_at DESC LIMIT 8")]
    a.close()
    return d


def _line(i, it, now) -> str:
    age = R.age_days(it["created"], now)
    total = (it["price"] or 0) + (it["ship"] or 0)
    head = f"{i}) {_rate(it):.1f}/day  GBP {total:.2f}  [{it['cls']}] {(it['title'] or '')[:58]}"
    if it["cls"] == "PROV":
        det = f"   {it['sold']} sold in {age:.0f} days (lifetime average)" if age is not None else f"   {it['sold']} sold"
    else:
        det = (f"   +{it['d_sold']} sold in {it['win_days']:.1f} selling days | lifetime {it['life_rate']:.1f}/day | "
               f"{it['sold']} sold" + (f" | listed {age:.0f}d ago" if age is not None else ""))
    return head + "\n" + det + f"\n   {it['url']}"


def report(c, top=15, limit=None) -> str:
    now = time.time()
    n = {r[0]: r[1] for r in c.execute("SELECT COALESCE(cls, '-'), COUNT(*) FROM items GROUP BY 1")}
    seen, snapped, tracking = c.execute("SELECT COUNT(*), COALESCE(SUM(snaps > 0), 0), COALESCE(SUM(status='tracking'), 0) "
                                        "FROM items").fetchone()
    cells = c.execute("SELECT COUNT(DISTINCT cat_id) FROM cells WHERE last_best IS NOT NULL OR last_watched IS NOT NULL").fetchone()[0]
    L = [f"MARKET RADAR {datetime.now(R.UK).strftime('%d %b %H:%M')}",
         f"Watching {cells} eBay categories | {seen} listings seen | {snapped} measured | {tracking} tracked",
         "Confirmed (sales counted between two snapshots): " + " | ".join(f"{k} {n.get(k, 0)}" for k in R.FAST_CLASSES),
         f"Provisional (average since listing, 2nd snapshot pending): {n.get('PROV', 0)}"]
    conf = c.execute(f"SELECT * FROM items WHERE cls IN {_FAST} ORDER BY rate DESC LIMIT ?", (top,)).fetchall()
    if conf:
        L += ["", "TOP CONFIRMED SELLERS (measured)"] + [_line(i, it, now) for i, it in enumerate(conf, 1)]
    half = max(top // 2, 4)
    fresh = [it for it in c.execute("SELECT * FROM items WHERE cls='PROV' ORDER BY life_rate DESC").fetchall()
             if (R.age_days(it["created"], now) or 999) <= 30][:half]
    if fresh:
        L += ["", "NEW AND ALREADY SELLING FAST (listed in the last 30 days)"] + [_line(i, it, now) for i, it in enumerate(fresh, 1)]
    done = {it["key"] for it in fresh}
    prov = [it for it in c.execute("SELECT * FROM items WHERE cls='PROV' ORDER BY life_rate DESC LIMIT ?", (half + len(done),))
            if it["key"] not in done][:half]
    if prov:
        L += ["", "ESTABLISHED BEST SELLERS (lifetime average, to be confirmed)"] + [_line(i, it, now) for i, it in enumerate(prov, 1)]
    hot = hot_types(c)
    if hot:
        L += ["", "HOT PRODUCT TYPES (several sellers selling fast)"] + [
            f"  {bg}: {cnt} listings, {s} sellers, about {rate:.0f}/day together" for bg, cnt, s, rate in hot]
    em, days = emerging(c)
    if em:
        L += ["", "EMERGING (sellers launching it yesterday vs the daily average before)"] + [
            f"  {t}: {a} vs {b}  ({root})" for t, a, b, root in em]
    elif em is None:
        L += ["", f"Trend watch: {days} of 3 days of history collected - emerging terms start after that"]
    f = funnel()
    listed = f.pop("_listed")
    if f:
        names = {"new": "waiting", "processing": "in progress", "listed": "LISTED", "no_match": "no Amazon match",
                 "not_viable": "no profit", "low_demand": "withdrawn", "not_listed": "listing refused", "error": "error"}
        L += ["", "LISTING FUNNEL (radar candidates): " + " | ".join(f"{names.get(k, k)} {v}" for k, v in sorted(f.items()))]
        L += [f"  listed: {x['title'][:50]} GBP {x['price']} ({x['radar_rate']}/day on eBay)" for x in listed]
    text = "\n".join(L)
    if limit and len(text) > limit:
        text = text[:limit - 20].rsplit("\n", 1)[0] + "\n(...cut)"
    return text


def send_digest(c, used, left):
    text = report(c, top=6, limit=3600) + f"\neBay API: {used} calls this run, {left} left today"
    try:
        from telegram_bot import _send
        _send(text)
    except Exception as e:
        print("digest not sent:", e)
        print(text)
