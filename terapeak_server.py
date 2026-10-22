"""
Terapeak Data Receiver — listens on port 8765.

The Chrome extension POSTs Terapeak research data here as you browse
Seller Hub. This server saves it to the local DB so the research cycle
can use real sold-price data instead of the bestMatch active-listing proxy.
"""
import json
import logging
import os
import sqlite3
from datetime import datetime, timezone

from flask import Flask, request, jsonify

from db import DB_PATH

log = logging.getLogger(__name__)
app = Flask(__name__)

API_KEY = "b1e7ff746dd94281ffac3ae6856f89c9"


def _cors(response):
    response.headers["Access-Control-Allow-Origin"] = "*"
    response.headers["Access-Control-Allow-Headers"] = "Content-Type, X-API-Key"
    response.headers["Access-Control-Allow-Methods"] = "POST, GET, OPTIONS"
    return response


@app.after_request
def after(response):
    return _cors(response)


@app.route("/terapeak", methods=["OPTIONS"])
def options():
    return jsonify({}), 200


def _auth():
    return request.headers.get("X-API-Key") == API_KEY


_SCHEMA = """
    CREATE TABLE IF NOT EXISTS terapeak_cache (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        keyword TEXT NOT NULL,
        avg_sold_price REAL,
        sell_through_pct REAL,
        total_listings INTEGER,
        total_sold INTEGER,
        avg_shipping REAL,
        date_range TEXT,
        fetched_at TEXT
    );
    CREATE INDEX IF NOT EXISTS idx_terapeak_kw ON terapeak_cache(keyword, fetched_at);
"""
# every agent database that should see this data (comma-separated paths in TERAPEAK_EXTRA_DBS)
# Terapeak data is kept for account 2 only (set TERAPEAK_DBS to a comma-separated list to change that)
TARGET_DBS = [p.strip() for p in os.getenv("TERAPEAK_DBS", "/var/www/ebay-agent-2/ebay_agent2.db").split(",") if p.strip()]


def _save(data: dict):
    keyword = (data.get("keyword") or "").strip().lower()
    if not keyword:
        return False
    row = (
        keyword,
        data.get("avg_sold_price"),
        data.get("sell_through_pct"),
        data.get("total_listings"),
        data.get("total_sold"),
        data.get("avg_shipping"),
        data.get("date_range", "90d"),
        datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
    )
    for path in TARGET_DBS:
        try:
            conn = sqlite3.connect(path)
            conn.executescript(_SCHEMA)
            conn.execute(
                """
                INSERT INTO terapeak_cache
                    (keyword, avg_sold_price, sell_through_pct, total_listings,
                     total_sold, avg_shipping, date_range, fetched_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                row,
            )
            conn.commit()
            conn.close()
        except Exception as e:
            log.warning(f"Terapeak save to {path} failed: {e}")
    log.info(
        f"Terapeak saved: '{keyword}' | avg_sold=£{data.get('avg_sold_price')} "
        f"| sell_through={data.get('sell_through_pct')}% | sold={data.get('total_sold')}"
    )
    return True


@app.post("/terapeak")
def receive():
    if not _auth():
        return jsonify({"error": "unauthorized"}), 401
    data = request.get_json(force=True, silent=True) or {}
    if not data:
        return jsonify({"error": "no data"}), 400
    ok = _save(data)
    if ok:
        return jsonify({"status": "saved", "keyword": data.get("keyword")}), 200
    return jsonify({"error": "missing keyword"}), 400


@app.get("/terapeak")
def list_cache():
    if not _auth():
        return jsonify({"error": "unauthorized"}), 401
    conn = sqlite3.connect(TARGET_DBS[0])
    rows = conn.execute(
        """
        SELECT keyword, avg_sold_price, sell_through_pct, total_sold, fetched_at
        FROM terapeak_cache
        ORDER BY fetched_at DESC LIMIT 100
        """
    ).fetchall()
    conn.close()
    return jsonify([
        {
            "keyword": r[0],
            "avg_sold_price": r[1],
            "sell_through_pct": r[2],
            "total_sold": r[3],
            "fetched_at": r[4],
        }
        for r in rows
    ])



_ITEM_SCHEMA = """
    CREATE TABLE IF NOT EXISTS watch_items (
        item_id TEXT PRIMARY KEY,
        url TEXT, title TEXT, price REAL, seller TEXT, seller_type TEXT, feedback INTEGER,
        sold_today INTEGER, sold_total INTEGER, in_baskets INTEGER, watchers INTEGER, available INTEGER,
        condition TEXT, brand TEXT, mpn TEXT, ean TEXT, model TEXT, images TEXT,
        captured_at TEXT, updated_at TEXT, status TEXT DEFAULT 'new', note TEXT, listing_id TEXT
    );
"""


def _save_item(d: dict) -> bool:
    item_id = str(d.get("item_id") or "").strip()
    title = (d.get("title") or "").strip()
    if not item_id or not title:
        return False
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    vals = (
        item_id, d.get("url"), title, d.get("price"), d.get("seller"), d.get("seller_type"), d.get("feedback"),
        d.get("sold_today"), d.get("sold_total"), d.get("in_baskets"), d.get("watchers"), d.get("available"),
        d.get("condition"), d.get("brand"), d.get("mpn"), d.get("ean"), d.get("model"),
        json.dumps(d.get("images") or []), now, now,
    )
    for path in TARGET_DBS:
        try:
            conn = sqlite3.connect(path)
            conn.executescript(_ITEM_SCHEMA)
            conn.execute(
                """
                INSERT INTO watch_items
                  (item_id, url, title, price, seller, seller_type, feedback, sold_today, sold_total, in_baskets,
                   watchers, available, condition, brand, mpn, ean, model, images, captured_at, updated_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(item_id) DO UPDATE SET
                  price=excluded.price, sold_today=excluded.sold_today, sold_total=excluded.sold_total,
                  in_baskets=excluded.in_baskets, watchers=excluded.watchers, available=excluded.available,
                  brand=COALESCE(NULLIF(excluded.brand,''), watch_items.brand),
                  mpn=COALESCE(NULLIF(excluded.mpn,''), watch_items.mpn),
                  ean=COALESCE(NULLIF(excluded.ean,''), watch_items.ean),
                  model=COALESCE(NULLIF(excluded.model,''), watch_items.model),
                  updated_at=excluded.updated_at
                """,
                vals,
            )
            conn.commit()
            conn.close()
        except Exception as e:
            log.warning(f"watch_items save to {path} failed: {e}")
    log.info(f"Item captured: {item_id} | {title[:50]} | GBP {d.get('price')} | sold_today={d.get('sold_today')} "
             f"sold={d.get('sold_total')} baskets={d.get('in_baskets')} watchers={d.get('watchers')}")
    return True


@app.route("/ebay_item", methods=["OPTIONS"])
def item_options():
    return jsonify({}), 200


@app.post("/ebay_item")
def receive_item():
    if not _auth():
        return jsonify({"error": "unauthorized"}), 401
    data = request.get_json(force=True, silent=True) or {}
    if _save_item(data):
        return jsonify({"status": "saved", "item_id": data.get("item_id")}), 200
    return jsonify({"error": "missing item_id/title"}), 400


@app.get("/ping")
def ping():
    return jsonify({"status": "ok"}), 200


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s — %(message)s")
    log.info("Terapeak receiver starting on port 8765")
    app.run(host="0.0.0.0", port=8765)
