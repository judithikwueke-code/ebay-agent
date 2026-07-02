import sqlite3
import os
from contextlib import contextmanager

DB_PATH = os.getenv("DB_PATH", "ebay_agent.db")


def init_db():
    with get_conn() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS products (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ebay_listing_id TEXT UNIQUE,
                ebay_offer_id TEXT UNIQUE,
                supplier TEXT NOT NULL,
                supplier_product_id TEXT NOT NULL,
                title TEXT,
                category TEXT,
                listed_price_gbp REAL,
                cost_gbp REAL,
                margin_pct REAL,
                status TEXT DEFAULT 'active',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS orders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ebay_order_id TEXT UNIQUE NOT NULL,
                product_id INTEGER REFERENCES products(id),
                supplier TEXT,
                supplier_order_id TEXT,
                tracking_number TEXT,
                buyer_name TEXT,
                buyer_address TEXT,
                sale_price_gbp REAL,
                cost_gbp REAL,
                profit_gbp REAL,
                is_furniture INTEGER DEFAULT 0,
                status TEXT DEFAULT 'pending',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                dispatched_at TIMESTAMP,
                delivered_at TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS health_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                date TEXT UNIQUE NOT NULL,
                defect_rate REAL,
                late_dispatch_rate REAL,
                cases_opened INTEGER,
                new_listings INTEGER DEFAULT 0,
                notes TEXT
            );

            CREATE TABLE IF NOT EXISTS supplier_performance (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                supplier TEXT NOT NULL,
                week TEXT NOT NULL,
                orders_placed INTEGER DEFAULT 0,
                orders_dispatched INTEGER DEFAULT 0,
                avg_dispatch_days REAL,
                defect_count INTEGER DEFAULT 0,
                UNIQUE(supplier, week)
            );

            CREATE INDEX IF NOT EXISTS idx_products_status ON products(status);
            CREATE INDEX IF NOT EXISTS idx_orders_status ON orders(status);
            CREATE INDEX IF NOT EXISTS idx_orders_ebay_id ON orders(ebay_order_id);
        """)


@contextmanager
def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def insert_product(supplier, supplier_product_id, title, category,
                   listed_price_gbp, cost_gbp, margin_pct,
                   ebay_listing_id=None, ebay_offer_id=None, status="active"):
    with get_conn() as conn:
        conn.execute("""
            INSERT OR IGNORE INTO products
            (ebay_listing_id, ebay_offer_id, supplier, supplier_product_id, title, category,
             listed_price_gbp, cost_gbp, margin_pct, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (ebay_listing_id, ebay_offer_id, supplier, supplier_product_id, title, category,
              listed_price_gbp, cost_gbp, margin_pct, status))


def publish_draft(ebay_offer_id, ebay_listing_id):
    """Promote a draft row to a live listing once it's actually published."""
    with get_conn() as conn:
        conn.execute("""
            UPDATE products SET ebay_listing_id = ?, status = 'active'
            WHERE ebay_offer_id = ?
        """, (ebay_listing_id, ebay_offer_id))


def is_product_already_listed(supplier: str, supplier_product_id: str) -> bool:
    """Returns True if this supplier product is already active in our listings."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT id FROM products WHERE supplier = ? AND supplier_product_id = ? AND status = 'active'",
            (supplier, supplier_product_id),
        ).fetchone()
        return row is not None


def get_product_by_listing(ebay_listing_id):
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM products WHERE ebay_listing_id = ?", (ebay_listing_id,)
        ).fetchone()
        return dict(row) if row else None


def insert_order(ebay_order_id, product_id, supplier, sale_price_gbp, cost_gbp,
                 buyer_name, buyer_address, is_furniture=False):
    profit = sale_price_gbp - cost_gbp
    with get_conn() as conn:
        conn.execute("""
            INSERT OR IGNORE INTO orders
            (ebay_order_id, product_id, supplier, sale_price_gbp, cost_gbp,
             profit_gbp, buyer_name, buyer_address, is_furniture)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (ebay_order_id, product_id, supplier, sale_price_gbp, cost_gbp,
              profit, buyer_name, buyer_address, int(is_furniture)))


def update_order(ebay_order_id, **kwargs):
    allowed = {"supplier_order_id", "tracking_number", "status", "dispatched_at", "delivered_at"}
    fields = {k: v for k, v in kwargs.items() if k in allowed}
    if not fields:
        return
    sets = ", ".join(f"{k} = ?" for k in fields)
    with get_conn() as conn:
        conn.execute(
            f"UPDATE orders SET {sets} WHERE ebay_order_id = ?",
            (*fields.values(), ebay_order_id)
        )


def get_pending_orders():
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM orders WHERE status IN ('pending', 'placed')"
        ).fetchall()
        return [dict(r) for r in rows]


def orders_without_tracking():
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT * FROM orders
            WHERE status = 'placed' AND tracking_number IS NULL
        """).fetchall()
        return [dict(r) for r in rows]


def count_listings_today():
    with get_conn() as conn:
        row = conn.execute("""
            SELECT COUNT(*) as cnt FROM products
            WHERE DATE(created_at) = DATE('now')
        """).fetchone()
        return row["cnt"]


def log_health(date, defect_rate, late_dispatch_rate, cases_opened, new_listings=0, notes=""):
    with get_conn() as conn:
        conn.execute("""
            INSERT INTO health_log (date, defect_rate, late_dispatch_rate, cases_opened, new_listings, notes)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(date) DO UPDATE SET
                defect_rate=excluded.defect_rate,
                late_dispatch_rate=excluded.late_dispatch_rate,
                cases_opened=excluded.cases_opened,
                new_listings=excluded.new_listings,
                notes=excluded.notes
        """, (date, defect_rate, late_dispatch_rate, cases_opened, new_listings, notes))
