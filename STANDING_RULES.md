# Standing rules for the eBay account 2 shop (yagz-emporium)

These are the owner's own instructions. `director.py` enforces them in code, and every session (human or Claude) must
read them first. If an instruction is not here it is not a rule yet: add it the moment the owner says it.

## Goal
- £100 profit per day (set 16 Nov 2026). Profit is tracked daily by the director against this number.
- Focus on high-value items: nothing under £25 is ever listed automatically; the target listing is £35+ with at least
  £10 profit per sale. Premium items (£80+) earn £15+ each.

## Never
1. Never lower a price. When Amazon's price rises, our price rises by the same amount, even if no longer competitive.
2. Never pause, zero-stock or end a LIVE listing unless the owner says so in plain words. "Stop the cheap ones" meant
   stop LISTING new cheap ones. Pause only when Amazon says out of stock.
3. Never put the word "Amazon" (or Amazon Basics etc.) anywhere in her eBay account: titles, descriptions, brand
   specifics, campaign names. (eBay UK also restricts dropshipping from other retailers; she knowingly accepted the risk.)
4. Never click our own sponsored listings or open our ads (it costs money).
5. Never raise ad rates above those she approved (2% default, 5% on high-value items that keep >= £12 profit) and never
   send buyer offers without her approval.
6. Never repeat buyers' personal data, never display API keys.
7. Never start a second browser: every browser job takes `/tmp/growth.lock` and runs under `systemd-run --scope -p MemoryMax=1300M`.
8. Never kill or restart other projects' processes (Findall, ApplyExpress) without asking.
9. Never run bulk AI jobs without checking API credit (Anthropic credit is empty; Groq writes listings).
10. Never save a token without verifying which eBay account it belongs to (account 3 exists).

## Always
- New listings: price >= £25 (MIN_LISTING_PRICE_GBP), profit >= £10 after the ad fee, real Amazon delivery date
  within 7 days, price sanity (<= 2.5x delivered cost), Prime for stoves/hobs, Telegram confirmation.
- Hot-selling products (radar finds, hunts): when a similar product is found on Amazon, list it at OUR profitable price
  (>= £10 profit after the ad fee). Never match or undercut the eBay seller's price (owner, 18 Nov). Sanity limit only:
  radar skips a candidate whose needed price is above 2x the rival's (probably a different product).
  Switch: RADAR_PRICE_FOR_PROFIT=1 (default) and RADAR_MAX_OVER_RIVAL=2.0 in .env.
- Built-in hobs say they are hard-wired, not plug-in. Gas stoves never say "flame-free" or "indoor".
- Check state after any interrupted call (a withdrawn/republished offer gets a new listing id).
- Always print `date -u`. Detach long jobs (nohup setsid) and never wait with a long foreground sleep.
- Handovers written for someone else start with "Written for: ...". Use they/them when pronouns are unknown.
- After every change that could affect live listings, verify on eBay (not only in the database).

## Decisions still waiting for the owner (do not decide for her)
- Restore or keep the 35 prices lowered on 13 Nov (record: /root/backup-ea2-1111/price_fix_1113.jsonl).
- Iwatani stove order and hand-plane order handling with the buyers.
- Dentalife: title change, barcode (EAN), pouch weight, postage cost per bundle.
- Which paused hobs/premium items (paused_review) return to sale; whether to delete the account-3 token backup.
- Server upgrade (memory is the main bottleneck); Telegram alerts instead of the broken email.

## Telegram (owner, 19 Nov 2026)
- NOT wanted: price/stock change digests, "NEW LISTING LIVE" alerts, "RESEARCH CYCLE COMPLETE", per-hunt "listed" messages.
  (switches: TELEGRAM_PRICE_ALERTS=1 / TELEGRAM_LISTING_ALERTS=1 in .env bring them back)
- Still sent: ORDER RECEIVED, real problems (WARNING lines, rule breaks), the 3 scoreboard reports a day, the radar digest.

## Order & cancellation reporter (owner, 23 Nov 2026)
- order_reporter.py (cron every 3 min, log order_reporter.log) reports ONLY new orders and cancellations/refunds. It never buys, ships or edits.
- Each detail (name, phone, address, postcode) is its own tap-to-copy line. eBay Collection Point orders (address contains eCP:XXXXXXXX) start with a bold ECP banner and the ECP number in its own copy block.
- It replaces the old ORDER RECEIVED / ORDER CANCELLED / unknown-listing alerts (LEGACY_ORDER_ALERTS=0 in .env switches the old ones off; =1 brings them back). Includes the Amazon buy link and quantity.
- LLM: the "Anthropic credits are exhausted" notice is off (LLM_CREDIT_ALERTS=1 brings it back); a WARNING is sent if EVERY AI provider fails (owner, 23 Nov).

## Dentalife CPC campaign (owner approved 23 Nov 2026)
- Campaign 169735020016 "Dentalife keyword test": daily budget stays GBP 3. Bids approved: up to GBP 0.45 exact / 0.35 phrase (raised 23 Nov from 0.25 / 0.20). 15 keywords, ads: bundle 366814763416 + pack of 8 (366819643483, also still in the 5% general campaign).
- Review on 26 Nov 19:30 UTC (ad_check --once sends it to Telegram): drop keywords with clicks but no sales, keep the ones that convert. Raising bids above the approved levels needs her approval.
- Quantity: 1 unit per listing (LISTING_QTY_CAP=1); a listing that has sold 2 units in one order, or 2 orders within an hour, may show 2 (config.sold_two, owner 23 Nov). Own-stock items (Dentalife) are not capped.
- Radar: only status new is processed (agent job every 30 min, 8 items, skipped while the browser lock is busy). Items refused by the selling limit (25002) or judged before the price-for-profit rule must be requeued to new (done 23 Nov: 62 not_viable + not_listed >= GBP 25).
