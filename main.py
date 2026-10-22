"""
Entry point — starts the scheduler and wires all modules together.

Usage:
  python main.py            # run the live agent
  python main.py --dry-run  # research + supplier match only, no real listings
"""

import argparse
import logging
import os
import sqlite3
import sys
from datetime import datetime, timezone
from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from config import (
    RESEARCH_HOUR, RESEARCH_HOUR_EVENING, HEALTH_CHECK_HOUR,
    ORDER_POLL_MINUTES, SUPPLIER_TRACKING_HOURS,
    DAILY_LISTING_CAP, CATEGORIES, SUPPLIER_PRIORITY, PRICING_MODE, MAX_PRICE_PREMIUM, PRICE_CAP_MARKUP,
    FLAT_PROFIT_GBP, FLAT_PROFIT_MAX_COST,
)
from db import init_db, is_product_already_listed
from research import find_opportunities
from supplier import find_best_supplier
from lister import create_listing
from orders import process_new_orders, sync_tracking
from monitor import run_health_check
from stock_monitor import run_stock_monitor
from telegram_bot import morning_briefing, evening_summary, alert_research_done, strategy_report
from cj_sweep import run_sweep

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(name)s — %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("ebay_agent.log"),
    ],
)
log = logging.getLogger(__name__)

_listing_paused = False


def _run_startup_research_if_missed():
    """
    On service start, check if today's research cycle was missed.
    A cycle counts as 'run today' if any product was listed today (UTC date).
    If nothing listed today and it's past 6am UK time, run immediately.
    """
    from db import DB_PATH as _DB
    from zoneinfo import ZoneInfo
    now_local = datetime.now(ZoneInfo("Europe/London"))
    if now_local.hour < RESEARCH_HOUR:
        return  # Too early — not missed yet
    today_str = now_local.strftime("%Y-%m-%d")
    try:
        conn = sqlite3.connect(_DB)
        count = conn.execute(
            "SELECT COUNT(*) FROM products WHERE status='active' AND created_at >= ?",
            (today_str + " 00:00:00",)
        ).fetchone()[0]
        conn.close()
    except Exception:
        count = 0
    if count == 0:
        log.info(
            f"Startup: research cycle was missed today (past {RESEARCH_HOUR}:00, "
            f"0 listings created today) — running now"
        )
        research_and_list()
    elif count < DAILY_LISTING_CAP and "cj" in SUPPLIER_PRIORITY:
        log.info(
            f"Startup: only {count} listings today vs cap {DAILY_LISTING_CAP} — "
            f"running CJ catalogue sweep to fill gap"
        )
        run_sweep()


def pause_listings():
    global _listing_paused
    _listing_paused = True
    log.warning("Auto-listing PAUSED — resolve health issues then restart")


def research_and_list(dry_run: bool = False):
    global _listing_paused
    if _listing_paused:
        log.warning("Listing is paused — skipping research cycle")
        return

    log.info("=== Research cycle starting ===")
    opportunities = find_opportunities(top_n=DAILY_LISTING_CAP * 5)

    listed = 0
    for opp in opportunities:
        if listed >= DAILY_LISTING_CAP:
            break

        is_furniture = opp["category"] in ("furniture", "mattress")
        fee_key = CATEGORIES.get(opp["category"], {}).get("fee_key", "everything_else")
        match = find_best_supplier(
            keyword=opp["keyword"],
            target_sell_price_gbp=opp["avg_price_gbp"],
            is_furniture=is_furniture,
            fee_key=fee_key,
            category=opp["category"],
        )
        if not match:
            log.info(f"No supplier match for '{opp['keyword']}' — skipping")
            continue

        if match["supplier"] == "amazon":
            # Search results only carry one thumbnail; read the product page for all photos + bullets.
            from amazon_source import get_product
            full = get_product(match["product_id"])
            if full and len(full.get("images", [])) > len(match.get("images", [])):
                match["images"] = full["images"]
                match["description"] = full.get("description") or match.get("description", "")
            if full and full.get("aspects") and os.getenv("AMAZON_BRAND_ASPECT", "1") == "1":
                match["aspects"] = full["aspects"]

        if is_product_already_listed(match["supplier"], match["product_id"]):
            log.info(f"Already listed: {match['supplier']} {match['product_id']} — skipping duplicate")
            continue

        flat_price = None
        if FLAT_PROFIT_GBP > 0 and not is_furniture:
            from config import total_cost_with_vat
            from supplier import flat_profit_price
            if total_cost_with_vat(match["cost_gbp"], match["shipping_gbp"]) <= FLAT_PROFIT_MAX_COST:
                flat_price = flat_profit_price(match["cost_gbp"], match["shipping_gbp"], fee_key, FLAT_PROFIT_GBP)
                match["min_sell_price_gbp"] = flat_price

        # Competitive pricing (account 2): price at what the cheapest quarter of rivals charge, after the
        # buyer-protection fee private-seller buyers pay on top; skip items that can't earn the minimum there.
        if os.getenv("COMPETITIVE_PRICING", "0") == "1" and not is_furniture:
            import math
            from config import total_cost_with_vat
            cps = opp.get("competitor_prices") or []
            if len(cps) >= 5:
                shelf = total_cost_with_vat(match["cost_gbp"], match["shipping_gbp"])
                min_profit = float(os.getenv("MIN_PROFIT_AT_P25", "2"))
                target = (cps[len(cps) // 4] - 0.36) / 1.0413
                if target < shelf + min_profit:
                    log.info(f"Can't compete '{opp['keyword']}': rivals' cheapest quarter ~£{target:.2f} vs our cost £{shelf:.2f} - skip")
                    continue
                def _nice(x, up):
                    n = math.floor(x)
                    c = [n - 1 + 0.99, n + 0.49, n + 0.99, n + 1.49]
                    c = [v for v in c if (v >= x - 1e-9 if up else v <= x + 1e-9)]
                    return round(min(c) if up else max(c), 2)
                flat_price = max(_nice(target, False), _nice(shelf + min_profit, True))
                match["min_sell_price_gbp"] = flat_price

        # Price competitiveness gate:
        # Our minimum viable price must be within 25% of the bestMatch market median.
        # If we can't fit in that band, we cannot compete — skip rather than list invisibly.
        market_price = opp["avg_price_gbp"]
        min_our_price = match.get("min_sell_price_gbp", 0)
        if not is_furniture and min_our_price > market_price * (MAX_PRICE_PREMIUM if PRICING_MODE == "cost_plus" else 1.25):
            log.info(
                f"Price gate FAIL '{opp['keyword']}': our min £{min_our_price:.2f} "
                f"vs market £{market_price:.2f} (+{(min_our_price/market_price-1)*100:.0f}%) — skip"
            )
            continue

        if match.get("price_floor_exceeded"):
            target_price = match["min_sell_price_gbp"]
        elif not is_furniture:
            target_price = market_price  # price AT market median, not just margin floor
        else:
            target_price = None

        if flat_price is not None:
            target_price = flat_price
            log.info(f"Priced '{opp['keyword']}' at £{flat_price} (rule-based: flat profit / competitor percentile)")
        elif PRICE_CAP_MARKUP > 0 and target_price is not None:
            import math
            from config import total_cost_with_vat
            from supplier import _min_viable_price
            cap = math.ceil(total_cost_with_vat(match["cost_gbp"], match["shipping_gbp"]) * PRICE_CAP_MARKUP) - 0.01
            floor = match.get("min_sell_price_gbp") or _min_viable_price(match["cost_gbp"], match["shipping_gbp"], fee_key)
            capped = round(max(min(target_price, cap), floor), 2)
            if capped < target_price:
                log.info(f"Price cap: '{opp['keyword']}' £{target_price} -> £{capped} (cost x{PRICE_CAP_MARKUP})")
            target_price = capped

        listing_id = create_listing(opp, match, dry_run=dry_run,
                                     target_sell_price_gbp=target_price)
        if listing_id:
            listed += 1

    try:
        from promote import promote_new_listings
        promote_new_listings()
    except Exception as e:
        log.warning(f"Promotion step skipped: {e}")

    skipped = len(opportunities) - listed
    log.info(f"=== Research cycle done — {listed} listing(s) created ===")
    try:
        alert_research_done(listed, skipped, DAILY_LISTING_CAP)
    except Exception:
        pass


def orders_job():
    log.info("--- Order poll ---")
    process_new_orders()


def tracking_job():
    log.info("--- Tracking sync ---")
    sync_tracking()


def health_job():
    log.info("--- Health check ---")
    run_health_check(pause_listings_fn=pause_listings)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true",
                        help="Research and match suppliers without creating real eBay listings")
    parser.add_argument("--once", action="store_true",
                        help="Run one research cycle then exit (useful for testing)")
    args = parser.parse_args()

    log.info("Initialising database...")
    init_db()

    if args.once or args.dry_run:
        log.info("Running single cycle (dry_run=%s)", args.dry_run)
        research_and_list(dry_run=args.dry_run)
        return

    # If the 6am research window already passed today and no listing was created
    # today yet, fire the research cycle immediately before starting the scheduler.
    # This handles the case where the service restarts after 6am (e.g. reboot, crash).
    _run_startup_research_if_missed()

    scheduler = BlockingScheduler(timezone="Europe/London")

    # Research + list: daily at 6am UK time.
    # misfire_grace_time=7200 means if the VPS was asleep and wakes up to find
    # the job was missed by up to 2 hours, it still runs immediately instead of
    # silently skipping until tomorrow.
    scheduler.add_job(
        research_and_list,
        CronTrigger(hour=RESEARCH_HOUR, minute=0, timezone="Europe/London"),
        id="research",
        name="Research & List (Morning)",
        replace_existing=True,
        misfire_grace_time=7200,
    )

    # Evening cycle: picks up new CJ stock added during the day + new niches
    scheduler.add_job(
        research_and_list,
        CronTrigger(hour=RESEARCH_HOUR_EVENING, minute=0, timezone="Europe/London"),
        id="research_evening",
        name="Research & List (Evening)",
        replace_existing=True,
        misfire_grace_time=3600,
    )

    # Order polling: every 15 minutes
    scheduler.add_job(
        orders_job,
        IntervalTrigger(minutes=ORDER_POLL_MINUTES),
        id="orders",
        name="Order Polling",
        replace_existing=True,
    )

    # Tracking sync: every 4 hours
    scheduler.add_job(
        tracking_job,
        IntervalTrigger(hours=SUPPLIER_TRACKING_HOURS),
        id="tracking",
        name="Tracking Sync",
        replace_existing=True,
    )

    # Health check: daily at 8am UK time
    scheduler.add_job(
        health_job,
        CronTrigger(hour=HEALTH_CHECK_HOUR, minute=0, timezone="Europe/London"),
        id="health",
        name="Health Check",
        replace_existing=True,
    )

    # Stock monitor: every 6 hours
    scheduler.add_job(
        run_stock_monitor,
        IntervalTrigger(hours=6),
        id="stock_monitor",
        name="Stock Monitor",
        replace_existing=True,
        misfire_grace_time=3600,
    )

    # CJ catalogue sweep: 10am, 2pm, 8pm — pages through all 10k+ UK products
    # This catches everything that keyword search misses (different product names etc.)
    for sweep_hour in ([10, 14, 20] if "cj" in SUPPLIER_PRIORITY else []):  # CJ-only job
        scheduler.add_job(
            run_sweep,
            CronTrigger(hour=sweep_hour, minute=0, timezone="Europe/London"),
            id=f"cj_sweep_{sweep_hour}",
            name=f"CJ Catalogue Sweep {sweep_hour}:00",
            replace_existing=True,
        )

    # eBay product pages captured by the Chrome extension -> exact Amazon match -> listing (account 2 feature)
    if os.getenv("WATCH_ENABLED", "0") == "1":
        def watch_items_job():
            from watch_scan import process_watch_items
            process_watch_items(limit=int(os.getenv("WATCH_LIMIT", "5")))
        scheduler.add_job(
            watch_items_job,
            IntervalTrigger(minutes=int(os.getenv("WATCH_INTERVAL_MIN", "45"))),
            id="watch_items",
            name="Captured eBay items -> Amazon match -> list",
            replace_existing=True,
            misfire_grace_time=1800,
        )

    # Telegram morning briefing: 7am daily (after 6am research cycle completes)
    scheduler.add_job(
        morning_briefing,
        CronTrigger(hour=7, minute=0, timezone="Europe/London"),
        id="tg_morning",
        name="Telegram Morning Briefing",
        replace_existing=True,
    )

    # Telegram evening summary: 7pm
    scheduler.add_job(
        evening_summary,
        CronTrigger(hour=19, minute=0, timezone="Europe/London"),
        id="tg_evening",
        name="Telegram Evening Summary",
        replace_existing=True,
    )

    # Weekly strategy report: every Sunday 8am
    scheduler.add_job(
        strategy_report,
        CronTrigger(day_of_week="sun", hour=8, minute=0, timezone="Europe/London"),
        id="tg_strategy",
        name="Telegram Strategy Report",
        replace_existing=True,
    )

    log.info(
        f"Scheduler started — research@{RESEARCH_HOUR}:00, "
        f"orders every {ORDER_POLL_MINUTES}min, "
        f"tracking every {SUPPLIER_TRACKING_HOURS}h, "
        f"health@{HEALTH_CHECK_HOUR}:00, "
        f"stock_monitor every 6h | cap={DAILY_LISTING_CAP}/day"
    )

    try:
        scheduler.start()
    except KeyboardInterrupt:
        log.info("Shutting down")
        scheduler.shutdown()


if __name__ == "__main__":
    main()
