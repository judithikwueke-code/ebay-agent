"""
Telegram Manager Bot for the eBay Dropship Agent.

Reports:
- Morning briefing (7am): overnight stats, today's plan, stock issues
- Evening summary (7pm): listings created, orders, revenue, next actions
- Real-time alerts: new order, listing live, stock issue, error

Also provides daily strategy intelligence:
- Top opportunities by score
- Supplier hit rate
- Category performance
- Account health
"""
import logging
import sqlite3
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import requests

from db import DB_PATH

log = logging.getLogger(__name__)

TOKEN = "8583121219:AAFbpza_GbFcfzjp8_mDZAGgWbZ5sAS9Z14"
CHAT_ID = "7681216735"
LONDON = ZoneInfo("Europe/London")


def _send(msg: str, silent: bool = False):
    try:
        requests.post(
            f"https://api.telegram.org/bot{TOKEN}/sendMessage",
            json={
                "chat_id": CHAT_ID,
                "text": msg,
                "disable_notification": silent,
            },
            timeout=10,
        )
    except Exception as e:
        log.warning(f"Telegram send failed: {e}")


def _db():
    return sqlite3.connect(DB_PATH)


def _today():
    return datetime.now(LONDON).strftime("%Y-%m-%d")


def _stats_today():
    conn = _db()
    today = _today()
    listed = conn.execute(
        "SELECT COUNT(*) FROM products WHERE status='active' AND created_at >= ?",
        (today + " 00:00:00",)
    ).fetchone()[0]
    oos = conn.execute(
        "SELECT COUNT(*) FROM products WHERE status='paused_oos'"
    ).fetchone()[0]
    total_active = conn.execute(
        "SELECT COUNT(*) FROM products WHERE status='active'"
    ).fetchone()[0]
    orders_today = conn.execute(
        "SELECT COUNT(*), COALESCE(SUM(sale_price_gbp),0), COALESCE(SUM(profit_gbp),0) "
        "FROM orders WHERE created_at >= ?",
        (today + " 00:00:00",)
    ).fetchone()
    orders_total = conn.execute(
        "SELECT COUNT(*), COALESCE(SUM(sale_price_gbp),0), COALESCE(SUM(profit_gbp),0) FROM orders"
    ).fetchone()
    conn.close()
    return {
        "listed_today": listed,
        "oos_paused": oos,
        "total_active": total_active,
        "orders_today": orders_today[0],
        "revenue_today": orders_today[1],
        "profit_today": orders_today[2],
        "orders_total": orders_total[0],
        "revenue_total": orders_total[1],
        "profit_total": orders_total[2],
    }


def _top_categories():
    conn = _db()
    rows = conn.execute(
        "SELECT category, COUNT(*) as cnt FROM products WHERE status='active' GROUP BY category ORDER BY cnt DESC"
    ).fetchall()
    conn.close()
    return rows


def _recent_listings(n=5):
    conn = _db()
    rows = conn.execute(
        "SELECT title, listed_price_gbp, margin_pct, supplier, created_at "
        "FROM products WHERE status='active' ORDER BY created_at DESC LIMIT ?",
        (n,)
    ).fetchall()
    conn.close()
    return rows


def morning_briefing():
    """7am daily briefing — plan for the day, overnight stats, strategy."""
    s = _stats_today()
    cats = _top_categories()
    recent = _recent_listings(3)
    now = datetime.now(LONDON)

    cat_lines = "\n".join(f"  {cat}: {cnt} listings" for cat, cnt in cats[:6])
    recent_lines = "\n".join(
        f"  {r[0][:40]} | £{r[1]:.2f} ({r[2]:.0f}%)"
        for r in recent
    )

    msg = (
        f"MORNING BRIEFING — {now.strftime('%a %d %b %Y')}\n"
        f"{'='*38}\n\n"
        f"SHOP STATUS\n"
        f"  Active listings : {s['total_active']}\n"
        f"  Paused (OOS)    : {s['oos_paused']}\n"
        f"  Orders all time : {s['orders_total']} | Revenue: £{s['revenue_total']:.2f}\n\n"
        f"TODAY'S PLAN\n"
        f"  06:00 Morning research cycle (up to 30 new listings)\n"
        f"  18:00 Evening research cycle (30 more)\n"
        f"  Stock monitor runs every 6h — pauses OOS listings automatically\n\n"
        f"TOP CATEGORIES\n{cat_lines}\n\n"
        f"LATEST LISTINGS\n{recent_lines}\n\n"
        f"STRATEGY TODAY\n"
        f"  Target: 50+ active listings by end of day\n"
        f"  Focus: new categories (pets, tech, baby, home decor, office)\n"
        f"  Suppliers: CJ UK warehouse (fast) + CJ China (15d handling)\n"
        f"  Monitoring: stock check, order poll every 15min\n\n"
        f"Any orders will be flagged instantly. Keep CJ wallet topped up."
    )
    _send(msg)


def evening_summary():
    """7pm daily summary — what was achieved, what's next."""
    s = _stats_today()
    cats = _top_categories()
    conn = _db()
    pending_orders = conn.execute(
        "SELECT COUNT(*) FROM orders WHERE status IN ('pending','placed','failed_placement')"
    ).fetchone()[0]
    conn.close()

    trend = "UP" if s["listed_today"] >= 10 else "SLOW" if s["listed_today"] >= 5 else "NEEDS ATTENTION"

    msg = (
        f"EVENING SUMMARY — {datetime.now(LONDON).strftime('%a %d %b')}\n"
        f"{'='*38}\n\n"
        f"TODAY'S RESULTS\n"
        f"  New listings    : {s['listed_today']} [{trend}]\n"
        f"  Active total    : {s['total_active']}\n"
        f"  OOS paused      : {s['oos_paused']}\n"
        f"  Orders today    : {s['orders_today']} | Revenue: £{s['revenue_today']:.2f}\n"
        f"  Profit today    : £{s['profit_today']:.2f}\n"
        f"  Pending orders  : {pending_orders} (need fulfilling)\n\n"
        f"ALL TIME\n"
        f"  Orders          : {s['orders_total']}\n"
        f"  Revenue         : £{s['revenue_total']:.2f}\n"
        f"  Est. profit     : £{s['profit_total']:.2f}\n\n"
        f"OVERNIGHT PLAN\n"
        f"  06:00 tomorrow: morning research cycle fires automatically\n"
        f"  Orders polled every 15min through the night\n"
        f"  Stock monitor at midnight and 06:00\n\n"
        + (f"ACTION: {pending_orders} order(s) need attention — check CJ wallet and place manually if needed\n" if pending_orders else "All orders handled.\n")
    )
    _send(msg)


def alert_new_listing(title: str, price: float, margin: float, supplier: str, ebay_id: str):
    _send(
        f"NEW LISTING LIVE\n"
        f"  {title[:50]}\n"
        f"  Price: £{price:.2f} | Margin: {margin:.0f}% | Via: {supplier}\n"
        f"  eBay: {ebay_id}",
        silent=True,
    )


def alert_new_order(title: str, sale: float, profit: float, buyer: str):
    _send(
        f"ORDER RECEIVED!\n"
        f"  {title[:50]}\n"
        f"  Sale: £{sale:.2f} | Profit: £{profit:.2f}\n"
        f"  Buyer: {buyer}\n"
        f"  Go to CJ and place order now — dispatch deadline in 5 days"
    )


def alert_stock_issue(title: str, ebay_id: str, issue: str):
    _send(
        f"STOCK ALERT — Listing paused\n"
        f"  {title[:50]}\n"
        f"  Issue: {issue}\n"
        f"  eBay qty set to 0 — listing invisible until restocked\n"
        f"  eBay ID: {ebay_id}"
    )


def alert_research_done(listed: int, skipped: int, cap: int):
    _send(
        f"RESEARCH CYCLE COMPLETE\n"
        f"  New listings: {listed}/{cap}\n"
        f"  Skipped (no match / already listed): {skipped}\n"
        f"  Next cycle: {'18:00' if datetime.now(LONDON).hour < 12 else '06:00 tomorrow'}",
        silent=True,
    )


def strategy_report():
    """Weekly strategy and performance report."""
    conn = _db()
    week_ago = (datetime.now(LONDON) - timedelta(days=7)).strftime("%Y-%m-%d")
    listed_week = conn.execute(
        "SELECT COUNT(*) FROM products WHERE status='active' AND created_at >= ?",
        (week_ago + " 00:00:00",)
    ).fetchone()[0]
    orders_week = conn.execute(
        "SELECT COUNT(*), COALESCE(SUM(sale_price_gbp),0), COALESCE(SUM(profit_gbp),0) "
        "FROM orders WHERE created_at >= ?",
        (week_ago + " 00:00:00",)
    ).fetchone()
    top_cats = conn.execute(
        "SELECT p.category, COUNT(o.id) as sales "
        "FROM orders o JOIN products p ON o.product_id=p.id "
        "WHERE o.created_at >= ? GROUP BY p.category ORDER BY sales DESC LIMIT 5",
        (week_ago + " 00:00:00",)
    ).fetchall()
    conn.close()

    cat_lines = "\n".join(f"  {cat}: {cnt} sales" for cat, cnt in top_cats) or "  No sales yet — keep listing"

    msg = (
        f"WEEKLY STRATEGY REPORT\n"
        f"{'='*38}\n\n"
        f"LAST 7 DAYS\n"
        f"  New listings   : {listed_week}\n"
        f"  Orders         : {orders_week[0]}\n"
        f"  Revenue        : £{orders_week[1]:.2f}\n"
        f"  Profit         : £{orders_week[2]:.2f}\n\n"
        f"BEST PERFORMING CATEGORIES\n{cat_lines}\n\n"
        f"GROWTH LEVERS (priority order)\n"
        f"  1. Get 100+ active listings — more shelf space = more sales\n"
        f"  2. Double down on categories with sales\n"
        f"  3. Request eBay seller limit increase once 10+ sales\n"
        f"  4. Activate Avasam (source products at app.avasam.com)\n"
        f"  5. Register BigBuy/Spocket for UK-stocked alternatives\n"
        f"  6. Top up CJ wallet — auto-ordering blocked without balance\n\n"
        f"TARGET: 200 active listings, 5 sales/week by end of month"
    )
    _send(msg)


if __name__ == "__main__":
    morning_briefing()
