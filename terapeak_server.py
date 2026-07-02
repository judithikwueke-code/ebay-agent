"""
Terapeak Data Receiver — listens on port 8765.

The Chrome extension POSTs Terapeak research data here as you browse
Seller Hub. This server saves it to the local DB so the research cycle
can use real sold-price data instead of the bestMatch active-listing proxy.
"""
import logging
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


def _save(data: dict):
    keyword = (data.get("keyword") or "").strip().lower()
    if not keyword:
        return False
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        """
        INSERT INTO terapeak_cache
            (keyword, avg_sold_price, sell_through_pct, total_listings,
             total_sold, avg_shipping, date_range, fetched_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            keyword,
            data.get("avg_sold_price"),
            data.get("sell_through_pct"),
            data.get("total_listings"),
            data.get("total_sold"),
            data.get("avg_shipping"),
            data.get("date_range", "90d"),
            datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
        ),
    )
    conn.commit()
    conn.close()
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
    conn = sqlite3.connect(DB_PATH)
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


@app.get("/ping")
def ping():
    return jsonify({"status": "ok"}), 200


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s — %(message)s")
    log.info("Terapeak receiver starting on port 8765")
    app.run(host="0.0.0.0", port=8765)
