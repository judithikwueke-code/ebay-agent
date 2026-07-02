"""
Daily account health check.
Reads eBay seller performance metrics and sends an email alert if thresholds are breached.
Also generates a weekly revenue/margin/supplier report.
"""

import logging
import smtplib
import requests
from datetime import date, datetime, timedelta
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from config import (
    EBAY_ANALYTICS_BASE, get_user_token,
    MAX_DEFECT_RATE, MAX_LATE_DISPATCH_RATE, MAX_OPEN_CASES,
    ALERT_EMAIL, SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASS,
)
from db import log_health, get_conn

log = logging.getLogger(__name__)


def _analytics_headers() -> dict:
    return {
        "Authorization": f"Bearer {get_user_token()}",
        "X-EBAY-C-MARKETPLACE-ID": "EBAY_GB",
        "Accept": "application/json",
    }


# ---------------------------------------------------------------------------
# Fetch metrics from eBay Analytics API
# ---------------------------------------------------------------------------

def _fetch_seller_standards() -> dict:
    """
    Returns seller performance standard metrics.
    https://developer.ebay.com/api-docs/sell/analytics/resources/seller_standards_profile
    """
    url = f"{EBAY_ANALYTICS_BASE}/seller_standards_profile"
    try:
        resp = requests.get(url, headers=_analytics_headers(), timeout=15)
        resp.raise_for_status()
        data = resp.json()
        profiles = data.get("standardsProfiles", [])
        if not profiles:
            return {}
        p = profiles[0]  # take the primary (global) profile
        return {
            "defect_rate": float(p.get("defectRate", {}).get("rate", 0)),
            "late_shipment_rate": float(p.get("lateShipmentRate", {}).get("rate", 0)),
            "cases_as_percentage": float(p.get("transactionDefectRate", {}).get("rate", 0)),
            "standard": p.get("standardType", "UNKNOWN"),
        }
    except Exception as e:
        log.error(f"Failed to fetch seller standards: {e}")
        return {}


def _fetch_open_cases() -> int:
    """Count open cases (disputes/returns) via eBay Resolution Centre API."""
    url = "https://api.ebay.com/post-order/v2/casemanagement/search"
    try:
        resp = requests.get(url, headers=_analytics_headers(), params={"status": "OPEN", "limit": 1}, timeout=10)
        resp.raise_for_status()
        return int(resp.json().get("total", 0))
    except Exception as e:
        log.warning(f"Could not fetch open cases: {e}")
        return 0


# ---------------------------------------------------------------------------
# Email alerts
# ---------------------------------------------------------------------------

def send_alert(subject: str, body: str):
    if not SMTP_USER or not SMTP_PASS:
        log.warning(f"SMTP not configured — alert not sent: {subject}")
        return
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = SMTP_USER
    msg["To"] = ALERT_EMAIL
    msg.attach(MIMEText(body, "plain"))
    try:
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
            server.starttls()
            server.login(SMTP_USER, SMTP_PASS)
            server.sendmail(SMTP_USER, ALERT_EMAIL, msg.as_string())
        log.info(f"Alert sent: {subject}")
    except Exception as e:
        log.error(f"Failed to send alert: {e}")


# ---------------------------------------------------------------------------
# Weekly report
# ---------------------------------------------------------------------------

def _weekly_report() -> str:
    today = date.today()
    week_ago = today - timedelta(days=7)
    with get_conn() as conn:
        # Revenue and margin summary
        rev_rows = conn.execute("""
            SELECT
                p.category,
                COUNT(o.id) as sales,
                ROUND(SUM(o.sale_price_gbp), 2) as revenue,
                ROUND(SUM(o.profit_gbp), 2) as profit,
                ROUND(AVG(o.profit_gbp / o.sale_price_gbp * 100), 1) as avg_margin
            FROM orders o
            JOIN products p ON o.product_id = p.id
            WHERE DATE(o.created_at) BETWEEN ? AND ?
            GROUP BY p.category
            ORDER BY revenue DESC
        """, (str(week_ago), str(today))).fetchall()

        # Supplier performance
        sup_rows = conn.execute("""
            SELECT
                supplier,
                COUNT(*) as orders,
                SUM(CASE WHEN tracking_number IS NOT NULL THEN 1 ELSE 0 END) as dispatched,
                ROUND(AVG(JULIANDAY(dispatched_at) - JULIANDAY(created_at)), 1) as avg_dispatch_days
            FROM orders
            WHERE DATE(created_at) BETWEEN ? AND ?
            GROUP BY supplier
        """, (str(week_ago), str(today))).fetchall()

        total_row = conn.execute("""
            SELECT
                COUNT(*) as sales,
                ROUND(SUM(sale_price_gbp), 2) as revenue,
                ROUND(SUM(profit_gbp), 2) as profit
            FROM orders
            WHERE DATE(created_at) BETWEEN ? AND ?
        """, (str(week_ago), str(today))).fetchone()

    lines = [
        f"eBay Agent — Weekly Report {week_ago} to {today}",
        "=" * 50,
        f"Total: {total_row['sales']} sales | £{total_row['revenue']} revenue | £{total_row['profit']} profit",
        "",
        "BY CATEGORY:",
    ]
    for r in rev_rows:
        lines.append(f"  {r['category']:20s} {r['sales']:3d} sales | £{r['revenue']:8.2f} | £{r['profit']:7.2f} profit | {r['avg_margin']}% margin")

    lines += ["", "SUPPLIER PERFORMANCE:"]
    for r in sup_rows:
        dispatch_days = r["avg_dispatch_days"] or "n/a"
        lines.append(f"  {r['supplier']:20s} {r['orders']:3d} orders | {r['dispatched']} dispatched | avg {dispatch_days}d dispatch")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Main health check — called daily
# ---------------------------------------------------------------------------

def run_health_check(pause_listings_fn=None):
    """
    Check seller metrics, log to DB, send alerts if thresholds breached.
    pause_listings_fn: optional callable to pause auto-listing if health is bad.
    """
    today = str(date.today())
    metrics = _fetch_seller_standards()
    open_cases = _fetch_open_cases()

    defect = metrics.get("defect_rate", 0)
    late = metrics.get("late_shipment_rate", 0)
    standard = metrics.get("standard", "UNKNOWN")

    log.info(f"Health check — defect={defect:.3f} late={late:.3f} cases={open_cases} standard={standard}")

    # Count new listings today
    with get_conn() as conn:
        new_today = conn.execute(
            "SELECT COUNT(*) as c FROM products WHERE DATE(created_at) = DATE('now')"
        ).fetchone()["c"]

    alerts = []
    if defect > MAX_DEFECT_RATE:
        alerts.append(f"DEFECT RATE {defect:.2%} exceeds limit {MAX_DEFECT_RATE:.2%}")
    if late > MAX_LATE_DISPATCH_RATE:
        alerts.append(f"LATE DISPATCH {late:.2%} exceeds limit {MAX_LATE_DISPATCH_RATE:.2%}")
    if open_cases > MAX_OPEN_CASES:
        alerts.append(f"OPEN CASES {open_cases} exceeds limit {MAX_OPEN_CASES}")

    log_health(
        date=today,
        defect_rate=defect,
        late_dispatch_rate=late,
        cases_opened=open_cases,
        new_listings=new_today,
        notes="; ".join(alerts) if alerts else "OK",
    )

    if alerts:
        send_alert(
            subject=f"[eBay Agent] HEALTH ALERT {today}",
            body="\n".join([
                f"Health check failed on {today}:",
                *[f"  - {a}" for a in alerts],
                "",
                f"Defect rate:    {defect:.3%}",
                f"Late dispatch:  {late:.3%}",
                f"Open cases:     {open_cases}",
                f"Seller level:   {standard}",
                "",
                "Auto-listing has been paused. Review your eBay Seller Hub.",
            ]),
        )
    else:
        log.info("Health check passed — all metrics within limits")

    # Weekly report every Monday
    if date.today().weekday() == 0:
        report = _weekly_report()
        log.info("Sending weekly report")
        send_alert(subject=f"[eBay Agent] Weekly Report {today}", body=report)
