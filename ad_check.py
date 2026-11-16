"""Check on the 'Dentalife keyword test' campaign WITHOUT touching the ad: where does the listing sit in eBay search, and what
did the campaign do (impressions, clicks, cost, sales, per keyword and per search query).

Everything is read through eBay's data feeds. Nothing here opens, views or clicks a listing page or an ad - clicking our own ad
is charged by eBay.

    python ad_check.py             # print the report
    python ad_check.py --telegram  # ... and send the summary to the owner's Telegram (what the scheduled run does)
    python ad_check.py --once      # scheduled use: also remove its own crontab line afterwards
"""
import csv
import datetime
import gzip
import io
import json
import os
import subprocess
import sys
import time

os.chdir("/var/www/ebay-agent-2")
sys.path.insert(0, "/var/www/ebay-agent-2")
import requests  # noqa: E402
import config  # noqa: E402,F401  (loads .env)
import stock_monitor as sm  # noqa: E402

CAMPAIGN = "169735020016"
LISTING = os.getenv("DENTALIFE_LISTING_ID", "366814763416")
B = "https://api.ebay.com/sell/marketing/v1"
H = dict(sm._sell_headers())
H["Content-Language"] = "en-GB"
lines = []


def out(s=""):
    print(s, flush=True)
    lines.append(s)


def position():
    tok = next(getattr(config, n)() for n in ("get_app_token", "get_application_token", "get_browse_token", "get_token") if hasattr(config, n))
    bh = {"Authorization": f"Bearer {tok}", "X-EBAY-C-MARKETPLACE-ID": "EBAY_GB"}
    out("WHERE WE SIT IN EBAY SEARCH (organic order from eBay's search feed; it cannot show the 'Sponsored' slots)")
    for q in ("dentalife cat", "dentalife cat treats", "purina dentalife cat treats", "purina dentalife", "cat dental treats"):
        res = []
        for off in (0, 100):
            r = requests.get("https://api.ebay.com/buy/browse/v1/item_summary/search", headers=bh, timeout=30,
                             params={"q": q, "limit": 100, "offset": off, "filter": "deliveryCountry:GB"})
            if r.status_code != 200:
                break
            res += r.json().get("itemSummaries", [])
        pos = next((i + 1 for i, it in enumerate(res) if LISTING in (it.get("legacyItemId") or it.get("itemId", ""))), None)
        out(f"  '{q}': {'position ' + str(pos) if pos else 'not in the first ' + str(len(res))} of {len(res)} results")


ASKED = {}                      # report type -> (task id, error): all reports are requested together, then collected
DEADLINE = [0.0]                # one shared time limit for waiting on eBay's reports


def ask(rtype, dims, metrics, days=3):
    now = datetime.datetime.now(datetime.timezone.utc)
    body = {"campaignIds": [CAMPAIGN], "marketplaceId": "EBAY_GB", "reportType": rtype, "reportFormat": "TSV_GZIP",
            "dateFrom": (now - datetime.timedelta(days=days)).strftime("%Y-%m-%dT00:00:00.000Z"),
            "dateTo": (now - datetime.timedelta(minutes=2)).strftime("%Y-%m-%dT%H:%M:%S.000Z"),   # eBay: the date cannot be in the future
            "fundingModels": ["COST_PER_CLICK"],
            "metricKeys": metrics, "dimensions": [{"dimensionKey": d} for d in dims]}
    r = requests.post(f"{B}/ad_report_task", headers=H, json=body, timeout=30)
    if r.status_code not in (200, 201, 202):               # 202 = accepted, the report is being built
        ASKED[rtype] = (None, f"could not ask for the report: {r.status_code} {r.text[:200]}")
    else:
        ASKED[rtype] = ((r.headers.get("Location") or "").rstrip("/").split("/")[-1], None)


def report(rtype, dims, metrics, days=3):
    if rtype not in ASKED:
        ask(rtype, dims, metrics, days)
    tid, err = ASKED[rtype]
    if err:
        return None, err
    status = None
    while True:
        t = requests.get(f"{B}/ad_report_task/{tid}", headers=H, timeout=30).json()
        status = t.get("reportTaskStatus")
        if status == "SUCCESS":
            break
        if status in ("FAILED", "ERROR"):
            return None, f"report failed: {t}"
        if time.time() > DEADLINE[0]:
            return None, f"eBay had not finished building this report yet (still {status}) - it will be ready later; ask me to read it"
        time.sleep(15)
    d = requests.get(f"{B}/ad_report/{t.get('reportId') or tid}", headers=H, timeout=60)   # the REPORT id, not the task id (eBay's href is internal)
    if d.status_code != 200:
        return None, f"could not download the report: {d.status_code} {d.text[:150]}"
    try:
        text = gzip.decompress(d.content).decode("utf-8", "replace")
    except OSError:
        text = d.content.decode("utf-8", "replace")
    return list(csv.DictReader(io.StringIO(text), delimiter="\t")), None


def campaign():
    c = requests.get(f"{B}/ad_campaign/{CAMPAIGN}", headers=H, timeout=20).json()
    out(f"\nCAMPAIGN: {c.get('campaignName')} | {c.get('campaignStatus')} | daily budget "
        f"£{c.get('budget', {}).get('daily', {}).get('amount', {}).get('value')} | started {c.get('startDate', '')[:16]}")
    kws = requests.get(f"{B}/ad_campaign/{CAMPAIGN}/keyword", headers=H, timeout=20).json().get("keywords", [])
    names = {k["keywordId"]: f"{k['keywordText']} ({k['matchType'].lower()})" for k in kws}
    metrics = ["cpc_impressions", "cpc_clicks", "cpc_ctr", "cpc_ad_fees_listingsite_currency", "cpc_attributed_sales"]
    DEADLINE[0] = time.time() + 22 * 60                  # eBay's reports can take many minutes: ask for all three, wait together
    ask("CAMPAIGN_PERFORMANCE_SUMMARY_REPORT", ["day", "campaign_id", "ad_group_id"], metrics)
    ask("KEYWORD_PERFORMANCE_REPORT", ["seller_keyword_id", "campaign_id", "ad_group_id", "keyword_match_type"], metrics)
    ask("SEARCH_QUERY_PERFORMANCE_REPORT", ["search_query", "campaign_id", "ad_group_id"], metrics)
    rows, err = report("CAMPAIGN_PERFORMANCE_SUMMARY_REPORT", ["day", "campaign_id", "ad_group_id"], metrics)
    out("\nCAMPAIGN TOTALS BY DAY (eBay's click charges can take up to 72 hours to show)")
    if err:
        out("  " + err)
    else:
        tot = {m: 0.0 for m in metrics if m != "cpc_ctr"}
        for r in rows:
            out("  " + " | ".join(f"{k}={v}" for k, v in r.items() if k in ("day",) + tuple(metrics)))
            for m in tot:
                try:
                    tot[m] += float(r.get(m) or 0)
                except ValueError:
                    pass
        if rows:
            out(f"  TOTAL: {tot['cpc_impressions']:.0f} impressions, {tot['cpc_clicks']:.0f} clicks, "
                f"£{tot['cpc_ad_fees_listingsite_currency']:.2f} spent, {tot['cpc_attributed_sales']:.0f} sale(s)")
        else:
            out("  (no rows yet)")
    rows, err = report("KEYWORD_PERFORMANCE_REPORT", ["seller_keyword_id", "campaign_id", "ad_group_id", "keyword_match_type"], metrics)
    out("\nBY KEYWORD")
    if err:
        out("  " + err)
    else:
        for r in rows:
            out(f"  {names.get(r.get('seller_keyword_id'), r.get('seller_keyword_id'))}: {r.get('cpc_impressions')} impr, "
                f"{r.get('cpc_clicks')} clicks, £{r.get('cpc_ad_fees_listingsite_currency')}, {r.get('cpc_attributed_sales')} sales")
        if not rows:
            out("  (no rows yet)")
    rows, err = report("SEARCH_QUERY_PERFORMANCE_REPORT", ["search_query", "campaign_id", "ad_group_id"], metrics)
    out("\nWHAT BUYERS ACTUALLY SEARCHED (that showed our ad)")
    if err:
        out("  " + err)
    else:
        for r in sorted(rows, key=lambda x: -float(x.get("cpc_impressions") or 0))[:10]:
            out(f"  '{r.get('search_query')}': {r.get('cpc_impressions')} impr, {r.get('cpc_clicks')} clicks")
        if not rows:
            out("  (no rows yet)")


def sold():
    import sqlite3
    o = requests.get("https://api.ebay.com/sell/fulfillment/v1/order", headers=H, timeout=30,
                     params={"filter": "creationdate:[2026-11-16T19:00:00.000Z]", "limit": 50}).json().get("orders", [])
    n = sum(1 for x in o for li in x.get("lineItems", []) if li.get("legacyItemId") == LISTING)
    out(f"\nSOLD since the campaign started: {n} bundle(s) of the Dentalife listing")


if __name__ == "__main__":
    out(f"AD CHECK {datetime.datetime.now(datetime.timezone.utc):%Y-%m-%d %H:%M} UTC - Purina Dentalife keyword campaign")
    for step in (position, campaign, sold):
        try:
            step()
        except Exception as e:
            out(f"  ({step.__name__} failed: {e})")
    if "--telegram" in sys.argv or "--once" in sys.argv:
        try:
            from telegram_bot import _send
            _send("\n".join(lines)[:3800], silent=True)
        except Exception as e:
            print("telegram failed:", e)
    if "--once" in sys.argv:           # a one-off: take its own line out of the crontab
        cur = subprocess.run(["crontab", "-l"], capture_output=True, text=True).stdout.splitlines()
        keep = [l for l in cur if "ad_check.py --once" not in l]
        subprocess.run(["crontab", "-"], input="\n".join(keep) + "\n", text=True)
