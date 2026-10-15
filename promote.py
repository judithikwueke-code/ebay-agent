"""
Add every live listing that is not yet in the promotion campaign to it (Promoted Listings Standard).
Needs PROMOTE_CAMPAIGN_ID (and optionally PROMOTE_RATE, default 2.0). Safe to re-run.
    python promote.py
"""
import logging
import os
import sqlite3

import requests

from config import get_user_token
from db import DB_PATH

log = logging.getLogger(__name__)
BASE = "https://api.ebay.com/sell/marketing/v1"


def promote_new_listings() -> int:
    campaign = os.getenv("PROMOTE_CAMPAIGN_ID")
    if not campaign:
        return 0
    rate = os.getenv("PROMOTE_RATE", "2.0")
    headers = {"Authorization": f"Bearer {get_user_token()}", "Content-Type": "application/json",
               "Accept": "application/json", "Content-Language": "en-GB"}
    try:
        conn = sqlite3.connect(DB_PATH)
        ids = [r[0] for r in conn.execute(
            "SELECT ebay_listing_id FROM products WHERE ebay_listing_id IS NOT NULL AND status='active' "
            "AND supplier != 'sportsdirect'")]
        conn.close()
        have = set()
        url = f"{BASE}/ad_campaign/{campaign}/ad?limit=500"
        r = requests.get(url, headers=headers, timeout=30)
        if r.status_code == 200:
            have = {a.get("listingId") for a in r.json().get("ads", [])}
        todo = [i for i in ids if i not in have]
        if not todo:
            return 0
        done = 0
        for i in range(0, len(todo), 100):
            chunk = todo[i:i + 100]
            body = {"requests": [{"listingId": x, "bidPercentage": rate} for x in chunk]}
            r = requests.post(f"{BASE}/ad_campaign/{campaign}/bulk_create_ads_by_listing_id",
                              headers=headers, json=body, timeout=60)
            if r.status_code == 200:
                done += sum(1 for x in r.json().get("responses", []) if x.get("statusCode") in (200, 201))
        log.info(f"Promoted {done} new listing(s) at {rate}%")
        return done
    except Exception as e:
        log.warning(f"Promotion step failed: {e}")
        return 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    print(promote_new_listings(), "listing(s) added")
