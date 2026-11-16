"""
Read-only account diagnostics: everything eBay tells us that affects how many buyers SEE the listings.

  account_check.py

  1. seller standards (below standard = search demotion)
  2. selling limits (how many more items / how much value may be listed this month)
  3. postage policy in use (handling time + service = the delivery date buyers see)
  4. promotion campaign state and ad count
  5. eBay's own suggested ad rate per listing (Recommendation API)      -> account_ads.json
  6. listings with interested buyers (watchers / baskets) that can be sent an offer (Negotiation API)
"""
import json
import os
import statistics

import requests

from config import EBAY_ANALYTICS_BASE, EBAY_FULFILLMENT_POLICY_ID, get_user_token
from db import get_conn

HERE = os.path.dirname(os.path.abspath(__file__))
ACCOUNT = "https://api.ebay.com/sell/account/v1"
MARKETING = "https://api.ebay.com/sell/marketing/v1"


def _h():
    return {"Authorization": f"Bearer {get_user_token()}", "Accept": "application/json",
            "Content-Type": "application/json", "Content-Language": "en-GB", "X-EBAY-C-MARKETPLACE-ID": "EBAY_GB"}


def _get(url, **kw):
    r = requests.get(url, headers=_h(), timeout=60, **kw)
    return r.status_code, (r.json() if r.text[:1] in "{[" else r.text[:300])


def suggested_ad_rates(listing_ids: list[str]) -> dict:
    """listing id -> {"item": %, "trending": %, "recommended": bool}"""
    out = {}
    for i in range(0, len(listing_ids), 100):
        r = requests.post("https://api.ebay.com/sell/recommendation/v1/find?filter=recommendationTypes:%7BAD%7D",
                          headers=_h(), json={"listingIds": listing_ids[i:i + 100]}, timeout=90)
        if r.status_code != 200:
            print("  recommendation API", r.status_code, r.text[:300])
            continue
        for rec in r.json().get("listingRecommendations") or []:
            ad = (rec.get("marketing") or {}).get("ad") or {}
            rates = {b.get("basis", "").lower(): float(b.get("value") or 0) for b in ad.get("bidPercentages") or []}
            out[str(rec.get("listingId"))] = {"item": rates.get("item"), "trending": rates.get("trending"),
                                              "recommended": ad.get("promoteWithAd") == "RECOMMENDED"}
    return out


def eligible_for_offers() -> list[str]:
    """Listings with interested buyers that eBay will let us send an offer on."""
    ids, offset = [], 0
    while True:
        code, d = _get(f"https://api.ebay.com/sell/negotiation/v1/find_eligible_items?limit=200&offset={offset}")
        if code != 200 or not isinstance(d, dict):
            if code != 204:
                print("  negotiation API", code, str(d)[:300])
            break
        ids += [str(x.get("listingId")) for x in d.get("eligibleItems") or []]
        offset += 200
        if offset >= int(d.get("total") or 0):
            break
    return ids


def main():
    print("=== 1. Seller standards ===")
    code, d = _get(f"{EBAY_ANALYTICS_BASE}/seller_standards_profile")
    for p in (d.get("standardsProfiles") or []) if isinstance(d, dict) else []:
        print(f"  {p.get('program')} / {p.get('cycle', {}).get('cycleType')}: {p.get('standardsLevel')}  "
              + ", ".join(f"{m.get('metricKey')}={m.get('value') if not isinstance(m.get('value'), dict) else m['value'].get('value')}"
                          for m in p.get("metrics") or [] if m.get("metricKey") in (
                              "DEFECTIVE_TRANSACTION_RATE", "SHIPPING_MISS_RATE", "CLAIMS_SAF_RATE", "TRANSACTIONS_COUNT",
                              "MIN_TXN_COUNT", "CASES_CLOSED_WO_SELLER_RESOLUTION")))
    if code != 200:
        print("  ", code, str(d)[:300])

    print("=== 2. Selling limits ===")
    code, d = _get(f"{ACCOUNT}/privilege")
    print("  ", code, json.dumps(d)[:400])

    print("=== 3. Postage policy ===")
    code, d = _get(f"{ACCOUNT}/fulfillment_policy/{EBAY_FULFILLMENT_POLICY_ID}")
    if code == 200:
        print(f"  {d.get('name')}: handling {d.get('handlingTime')}")
        for o in d.get("shippingOptions") or []:
            for s in o.get("shippingServices") or []:
                print(f"    {o.get('optionType')} {s.get('shippingServiceCode')} cost {s.get('shippingCost', {}).get('value')} "
                      f"free={s.get('freeShipping')}")
    else:
        print("  ", code, str(d)[:300])

    print("=== 4. Promotion campaign ===")
    campaign = os.getenv("PROMOTE_CAMPAIGN_ID")
    code, d = _get(f"{MARKETING}/ad_campaign/{campaign}")
    if code == 200:
        print(f"  {d.get('campaignName')}: {d.get('campaignStatus')} funding {d.get('fundingStrategy')} "
              f"start {str(d.get('startDate'))[:10]} end {str(d.get('endDate'))[:10]}")
    else:
        print("  ", code, str(d)[:300])
    code, d = _get(f"{MARKETING}/ad_campaign/{campaign}/ad?limit=500")
    ads = (d.get("ads") or []) if isinstance(d, dict) else []
    rates = sorted(float(a.get("bidPercentage") or 0) for a in ads)
    print(f"  ads in campaign: {d.get('total') if isinstance(d, dict) else d}; rates min {rates[0] if rates else '-'} "
          f"max {rates[-1] if rates else '-'}; statuses {sorted({a.get('adStatus') for a in ads})}")

    with get_conn() as c:
        prods = {str(r["ebay_listing_id"]): dict(r) for r in c.execute(
            "SELECT ebay_listing_id, title, listed_price_gbp, cost_gbp, supplier FROM products "
            "WHERE status='active' AND ebay_listing_id IS NOT NULL")}

    print("=== 5. eBay's suggested ad rates ===")
    sug = suggested_ad_rates(list(prods))
    json.dump(sug, open(os.path.join(HERE, "account_ads.json"), "w"))
    item = [v["item"] for v in sug.values() if v.get("item")]
    trend = [v["trending"] for v in sug.values() if v.get("trending")]
    for name, xs in (("item-based", item), ("trending", trend)):
        if xs:
            xs.sort()
            print(f"  {name}: {len(xs)} listings, min {xs[0]}, quarter {xs[len(xs) // 4]}, median {statistics.median(xs)}, "
                  f"three-quarters {xs[len(xs) * 3 // 4]}, max {xs[-1]}")
    print(f"  eBay marks {sum(1 for v in sug.values() if v['recommended'])} of {len(sug)} as worth promoting")

    print("=== 6. Listings with interested buyers (offers can be sent) ===")
    elig = eligible_for_offers()
    print(f"  {len(elig)} listing(s)")
    for lid in elig:
        p = prods.get(lid, {})
        print(f"    {lid}  GBP {p.get('listed_price_gbp')}  cost ex-VAT {p.get('cost_gbp')}  {(p.get('title') or '?')[:60]}")


if __name__ == "__main__":
    main()
