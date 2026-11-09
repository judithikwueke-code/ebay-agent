# eBay Agent 2 — Session Context
_Last updated: 2026-11-03_

## Account
- **eBay account:** yagz-emporium (PRIVATE — zero selling fees)
- **Agent path:** /var/www/ebay-agent-2/
- **DB:** /var/www/ebay-agent-2/ebay_agent2.db (note: NOT ebay_agent.db — that file is empty)
- **Server:** 204.168.194.157

## Margin formula (private account)
```
profit = sell_price - source_cost
```
No eBay FVF. No payment processing fee to deduct. Simple subtraction.

## VAT handling in lister.py
- VAT_REGISTERED = False in config.py
- lister.py adds 20% VAT internally via total_cost_with_vat()
- Always pass cost_gbp = retail_price / 1.2 (ex-VAT) so lister doesn't double-count
- AliExpress prices say "Price includes VAT" → pass aliexpress_price / 1.2 as cost_gbp

## Live listings (as of 2026-11-02)
| eBay ID | Product | Price | ASIN |
|---|---|---|---|
| 366779514991 | deAO Baby Doll Stroller Set | £41.01 | B0BVW42WT6 |
| 366779514443 | Bayer Pink Stroller Set 10pc | £48.59 | B09HQS7CZG |
| 366779512694 | Bayer Cosy Baby Doll Pram | £45.22 | B08KQ9Q22C |
| 366777173706 | Bebeconfort Baby Buggy (real pushchair) | £91.99 | B0GF2T3477 |
| 366777172623 | GLOKID 3-in-1 Pram (real pushchair) | £192.34 | B0D3PTLYBP |
| 366777172501 | YAZOCO 3-in-1 Pram (real pushchair) | £192.27 | B0G2LYWGPZ |
| 366777169779 | Baby Alive Doll (repriced to £5 profit) | £32.10 | B0BDHM2XHM |
| + Christmas decorations | Various | £7-37 | Various |

**Note:** 3 real pushchairs (YAZOCO/GLOKID/Bebeconfort) remain listed — user said NOT to delist.

## Pending work
### 1. AliExpress bike listings (IN PROGRESS)
Two bikes sourced from AliExpress Local+ (UK warehouse, 2-3 day delivery):

**16" Pedal Bike** (item 1005009239318974)
- Title: 16 inch Children's Bicycle with Training Wheel for 5-8 Year-Old Kids
- 1111 Sale price: £68.53 (ends Nov 18 23:59)
- 4.4 stars, 343 sold
- Sell on eBay at: £90-95 → profit ~£22-27
- Image 1 converted: http://204.168.194.157/product-images/pedal_bike_16_1.jpg
- STATUS: LIVE — eBay ID 366779751425, £92, promoted 2%

**12" Balance Bike** (item 1005012481747429)
- Title: 12" Kids Balance Bike Walking Running Training Bicycle For 2-6 Years Children
- Seller: Crystals GB Official / Denny Shop UK Store
- 1111 Sale price: £16.53 (ends Nov 18 23:59) — WARNING: "only 2 left"
- 4.9 stars, 2,000+ sold
- Sell on eBay at: £32-38 → profit ~£16-22
- Designs available: Design 1-4 (Design 3 = pink)
- STATUS: LIVE — eBay ID 366779763451, £34.99, promoted 2%

### 2. Pending orders to fulfil
- **Douglas Bailey** — Head Torch 2-Pack — ASIN B0BLVT2Y9N — £15.49
- **Raluca Citirica** — Word Family Flip Books — ASIN B0FH6B9Z26 — £25.49
- **Denise Taylor** — Christmas Placemats — ASIN B0992MCVNR — £11.99

## Image pipeline for AliExpress products
AliExpress blocks scraping. Workflow:
1. User right-clicks product image → Copy image address
2. Download on server: `curl -o /tmp/bike.webp "URL"`
3. Convert: `/var/www/ebay-agent/venv/bin/python3 -c "from PIL import Image; Image.open('/tmp/bike.webp').convert('RGB').save('/var/www/html/product-images/bike.jpg', 'JPEG', quality=90)"`
4. Serve at: `http://204.168.194.157/product-images/filename.jpg`
5. Pass those URLs in supplier_match['images'] to create_listing()

## Key technical notes
- Playwright is in /var/www/ebay-agent/venv/ (NOT ebay-agent-2 venv)
- PIL/Pillow installed in /var/www/ebay-agent/venv/
- nginx serving static files at /var/www/html/ → http://204.168.194.157/
- VeRO filter uses word-boundary regex (buggy fix: 'buggy' no longer triggers 'ugg' brand)
- lister.py create_listing() requires supplier_match keys: supplier, product_id, title, cost_gbp, shipping_gbp, total_cost_gbp, product_url, images, description, margin_pct, uk_warehouse, delivery_days
- eBay inventory PUT requires header: Content-Language: en-GB
- extra_keywords.txt has 10 baby_kids keywords queued for automated cycle

## Research completed this session
- Scanned 20 baby/kids toy keywords → top opportunities: Baby Alive (£45 avg), Wooden dolls house (£59.95), Kids metal detector (£40.79)
- Kids bikes eBay market: £87-97 avg (77-226 sellers)
- Fat tire MTB 26": £259.90 avg (13-39 sellers, 674 watching)
- AliExpress Local+ confirmed viable sourcing channel for bikes (UK warehouse, free shipping, 2-3 days)

## Image hosting (updated 2026-11-02)
- Images served from: https://yagz.uk/product-images/
- Physical path: /var/www/yagz.uk/product-images/
- Convert WebP→JPEG: /var/www/ebay-agent/venv/bin/python3 with PIL
- Always use HTTPS yagz.uk URLs — eBay rejects HTTP and bare IPs

## AliExpress fulfillment policy
- Policy ID: 299622940015
- Name: AliExpress UK Dropship 4-5 days
- Handling time: 4 days (covers 1-day processing + AliExpress 2-3 day UK warehouse shipping)
- Use this policy for all AliExpress Local+ sourced listings


---
## Update 2026-11-03 — order alert reliability (account 2)
**Incident:** 2 sales today, no Telegram alert received.
- Sale 02-15395-90743 (Favoto Motorcycle Cover, listing 366023550043, SKU cl_7227095): listing created 2025-11-27 by another tool BEFORE this agent, so not in `products` -> `process_new_orders` logged "not in DB" and `continue`d with no alert.
- Sale 23-15356-89638 (Mini Magnetic Tiles, ASIN B0H3ZBHLZY, listing 366736194709, status paused_price): recorded OK. The logged "Failed to send alert: Connection unexpectedly closed" was the SMTP EMAIL alert (email is known broken; Telegram is the working channel). No Telegram failure was logged, cause of the missed message unknown; `_send` previously ignored HTTP status.
- Both orders were resent manually to Telegram and the user bought both on Amazon.

**Fixes deployed to /var/www/ebay-agent-2 (backups: `orders.py.bak-alertfix`, `telegram_bot.py.bak-alertfix`):**
1. `telegram_bot._send` retries 3x, checks HTTP status, logs rejects/give-up, returns True/False.
2. `orders.py`: orders for listings not in DB now send a Telegram "ORDER FOR UNKNOWN LISTING" alert (title, price, listing id, SKU, address), once per order (`unknown_order_alerts.txt`, only marked when send succeeds).
3. Order lookback widened 20 -> 180 min (`_fetch_new_ebay_orders`), with `_order_exists()` DB guard so already-processed orders are skipped (no duplicate alerts / Amazon orders).
- NOT applied to account 1 (/var/www/ebay-agent): its orders.py/telegram_bot.py differ; listings paused there.

**Open items**
- Upload tracking for 23-15356-89638 and 02-15395-90743 (`python upload_tracking.py <order> <TRACKING> 'Royal Mail'`; cover order not in DB so no auto status sync).
- Fix SMTP email alerts or remove the dependency (monitor.send_alert).
- Product 30 (magnetic tiles) is paused_price: Amazon cost rose above the listed price.

## Sync / environment notes
- Source of truth: this folder is a git repo (origin git@github.com:judithikwueke-code/ebay-agent.git, branch master, auto-save commits). Server code dir /var/www/ebay-agent-2 is NOT a git repo: pull changes with tar-over-ssh, deploy with scp + `systemctl restart ebay-agent-2`.
- SSH: `ssh -i ~/.ssh/hetzner_deploy root@204.168.194.157` (key is per machine; copy it or add a new key on another machine).
- Services: ebay-agent, ebay-agent-2, terapeak-server (port 8765, runs /var/www/ebay-agent/terapeak_server.py = local copy), ebay-webhook.
- Server runs Python 3.12 (venv /var/www/ebay-agent/venv); supplier.py uses 3.12-only nested f-string quotes, so local `py_compile` fails on older Python - not a bug on the server.
- .env files are gitignored; account 2 tokens/IDs live in server `/var/www/ebay-agent-2/.env` and local `.env.account2*`.
- Telegram: bot MyEbay_Agent_bot, chat id in .env; messages are prefixed with ACCOUNT_LABEL.

## Update 2026-11-03 (evening) — UK "Be Ready" campaign
- Gov campaign launched 2026-10-22 (3 days' supplies: water, food, torch, wind-up/battery radio, power bank, blankets, cash, first aid, documents). Built to capitalise:
  - `beready_list.py` (supplied single-item keywords + user-spotted winners: tactical LED flashlights, gas stove), `campaign_discover.py` (self-directed: Claude brainstorm + eBay best-seller mining + `campaign_keywords_manual.json` (137 hand-written keywords)), `campaign_preflight.py`, `run_campaign.sh` (preflight -> seed user-picked ASINs -> beready_list -> campaign_discover), `run_campaign_later.sh` (waits for quota reset).
  - User-picked products: B0D798PJJ9 Ember Rock 22-in-1 survival kit (£45.69), B0746GHQ2N General Medi 92pc mini first aid kit (£6.78) via `seed_amazon.py`.
  - Combos allowed if promising (user OK). `EXCLUDE_COMBO=1` env restricts to single items (supplier._is_combo_title).
  - `research.find_opportunities(only_keywords=...)` and `main.research_and_list(opportunities=, cap=)` allow targeted runs.
- **Gotchas found:** account 2 `.env` has `SEASONAL_ONLY=1` (lists only Christmas/terapeak categories) - campaign scripts set SEASONAL_ONLY=0. eBay Browse API daily quota (429) was exhausted on 2026-11-03 by research runs (resets 08:00 UTC; ~7 calls per keyword) - budget keyword counts. **Anthropic credit balance too low** (brainstorm + listing text need Claude) - top up at console.anthropic.com or listing fails.
- Run scheduled on server for 2026-11-04 08:05 UTC (`run_campaign_later.sh`); preflight aborts + Telegram-alerts if credits/quota still down. Logs: /tmp/campaign_*.log, /tmp/beready_live2.log; flag /tmp/campaign_done.flag.

## Telegram screenshot -> source -> list (added 2026-11-03)
- Service `telegram-listener` (systemd, account 2 dir, MemoryMax=200M) runs `telegram_listener.py`: long-polls the bot, obeys ONLY TELEGRAM_CHAT_ID. Send a product screenshot (optional caption) or an Amazon UK link/ASIN.
- Heavy work runs in `sourcing_job.py` subprocesses under `systemd-run --scope -p MemoryMax=1000M` (one at a time): `find` (Claude vision -> Amazon UK search -> Claude picks exact match -> price preview), `plan` (known ASIN), `list` (create listing). Nothing lists until the user presses "List it" (inline button); "Skip" / alternative-product buttons supported; previews expire after 24h (telegram_pending.json).
- `seed_amazon.py` refactored: `prepare()` (validate + price, lists nothing, raises SeedSkip) + `seed()` (prepare + create_listing). CLI unchanged. Backup on server: seed_amazon.py.bak-prepare.
- Tested 2026-11-03: vision + Amazon match OK on a real screenshot; price/list steps blocked by eBay 429 quota at the time - re-test after 08:00 UTC reset.
- Logs: `journalctl -u telegram-listener`. Restart: `systemctl restart telegram-listener`.
- 2026-11-03: main.research_and_list now returns early if PAUSE_LISTINGS flag exists (was only checked in lister.create_listing AFTER full research, wasting eBay/CJ/Claude calls on paused account 1). Deployed to account 1 (restarted); account 2 file updated, takes effect on next restart (no flag there).

## API quota guard (2026-11-03) - research.py, account 2
- eBay Browse API = 5,000 calls/day for the whole app (both agents), resets 08:00 UTC; check with Analytics getRateLimits (`/developer/analytics/v1_beta/rate_limit/?api_context=buy&api_name=Browse`). Daily cycles + extra runs had exhausted it (6,090 counted).
- `find_opportunities` now: (1) `DEMAND_SAMPLE_ITEMS=3` (was 6 -> ~4 calls/keyword instead of 7); (2) caches each keyword's outcome 48h in table `research_cache` (`RESEARCH_CACHE_HOURS`; failed/429/unknown-velocity results are NOT cached); (3) stops the cycle cleanly on a 429 and Telegram-alerts; (4) stops when remaining quota < `QUOTA_RESERVE` (600; campaign scripts use 250) so the Telegram bot always has calls. Backups: research.py.bak-quota, .env.bak-quota.
- Not yet done: ask eBay for a higher limit (developer portal Application Growth Check - needs the account owner).

## Be Ready campaign run (2026-11-04 12:42-14:10 UTC) - results
- Run delayed: 08:05 UTC preflight tripped on a still-throttled eBay quota; re-run manually at 12:42 UTC (quota was fully reset).
- 20 listings created: survival kit B0D798PJJ9 (366784594061, £61.69 - ABOVE market £44.45, floor-priced, unlikely to sell), mini first aid kit B0746GHQ2N (366784943884, £13.99), 6 from beready_list (wind-up/solar radios, power bank, torches, jerry can, camping stove, head torch), 12 from campaign_discover (377 keywords researched; hand crank charger, fire blanket, bivvy bag, sleeping mat, candles, cash box, thermometer, etc.).
- Flagged for review: inflatable "fire extinguisher" novelty prop (366784766999), pop-up gazebo £75.49 (bulky, off-theme), flat pry bar, survival kit price. No supplier / uncompetitive: tactical LED flashlights, led torch high power, portable gas stove (priced variant listed instead), money belt.
- eBay rejected the first-aid kit with "references MHRA approval"; `lister.create_listing` now scrubs MHRA/regulator references from title+description (backup lister.py.bak-mhra).

## Telegram bot: links from any source (2026-11-05)
- Bot accepts: screenshots; ANY link (Amazon incl. amzn.to/a.co/amzn.eu short links - redirects are followed; eBay /itm/ links via Browse API; other shops via page title/OG meta, browser fallback); several links per message (up to 6, processed one at a time); plain-text descriptions; bare ASINs. New sourcing_job.py modes `link` and `text`; `find` now shares `source_from_spec()`.
- Sites that block scraping (e.g. AliExpress) reply "Couldn't read that page - send a screenshot instead". Unknown which links the user first sent that failed (likely app short links or non-Amazon shops).
- Telegram bot token is now redacted from listener error logs (it was printed in a poll-error URL). Backups: *.bak-links on server.

## Own-store listings (2026-11-05) - list_own.py
- `list_own.py <product-url> <category_key> "<ebay keyword>" [--price N] [--title "exact title"] [--dry-run]` lists the user's OWN products (e.g. fabdealz.co.uk, schema.org Product JSON-LD) at the page price: no sourcing, margin gate or £12 minimum (lister honours `supplier_match["own_stock"]`; `title_override` forces an exact title). Supplier recorded as `fabdealz`; stock monitors ignore it; orders alert "manual fulfilment required". Cost stored as 0 (profit in order alerts is overstated). Quantity = DEFAULT_LISTING_QUANTITY (5). Promoted 2% automatically.
- Listed: 366787454518 Silicone Kitchen Faucet Absorbent Mat... £8.99 (user-specified title); 366787455299 Cat Dental Treats Toy Bundle (Dentalife pouches + sisal sticks) £14.99.
- fabdealz.co.uk is a Next.js/Medusa store (no Shopify .json); product data is in JSON-LD on the page.

## Mat listing changes + image hosting finding (2026-11-05)
- Faucet mat 366787454518: price £5.99, buyer pays postage via NEW fulfilment policy 299830794015 "Buyer pays: Royal Mail 2nd class GBP 2.99", "GET 2 FOR THE PRICE OF 1" banner on main image + description note (every order = 2 mats). Title unchanged (80 chars, no room).
- **Image hosting:** `http://204.168.194.157/product-images/...` no longer works (nginx only serves named sites since the server hosts fabdealz/applyexpress/linkedpen), so the old manual image workflow is dead. Use eBay's Media API instead: `POST https://apim.ebay.com/commerce/media/v1_beta/image/create_image_from_file` (multipart `image`) or `create_image_from_url` (JSON `{"imageUrl":...}`) with the user token -> returns Location; GET it for `imageUrl` (i.ebayimg.com). eBay forbids MIXING self-hosted and EPS pictures in one listing (error 25014): convert all or none.
- Cat bundle competitive check: nobody else sells the exact bundle; Dentalife 40g pouch from £2.95 (free post), 5x140g packs from £20.76.

## Growth engine (2026-11-05) - user: "intensify effort, build sales"
- Sales review: the 22-in-1 survival kit SOLD at £61.69 (profit £14.51) despite being priced ~39% above the eBay market average -> premium pricing on niche/kit items can work; don't assume above-market won't sell. Best categories by orders so far: home_living (8), seasonal (4). Head torch (Blukar) sold twice. eBay selling limit £37,800 / 5,000 items (not a constraint); 136 active listings.
- `campaign_discover.py` is now a generic theme engine: `"<theme>" [--cap N] [--seeds a,b] [--manual file.json]`. Claude brainstorms ~200 keywords AND is told what has already sold (orders table) so it finds siblings of winners; seed queries for eBay best-seller mining are generated per theme; kits/bundles allowed; DAILY_LISTING_CAP defaults to 150 for these runs (env, set before config loads).
- `run_growth.sh [themes=2] [cap=30] [start]` runs N themes from `growth_themes.txt` (rotating by day), preflight first, flock-protected, logs /tmp/growth_YYYY-MM-DD.log, then `growth_summary.py` sends a Telegram digest (new listings, orders, profit, API calls left). **Cron: 08:30 UTC daily `run_growth.sh 3 30`** (quota resets 08:00 UTC). First run 2026-11-05 20:12 UTC: 4 themes using the day's unused quota (~4,970 calls).
- Edit `growth_themes.txt` to steer it; remove the cron line (`crontab -e`) to stop it.
- Ideas not built yet: premium-price probe (list a few items above market, since the survival kit sold), repricing/ending slow listings after N days, auto-boosting ad rate on listings with watchers, reorder alerts.

## Pack-size sourcing error found (2026-11-05)
- Listing 366747572052 "Blukar Head Torch Rechargeable 2 Pack" was sourced from ASIN B0BLVT2Y9N which is the SINGLE-torch variation (~£11); the real 2-pack costs £14.99 on Amazon (was £13.99 before 5 Nov). So profit in DB/alerts was overstated: 3 orders at £15.49 had real margin ~£0.5-1.5. Repriced to £21.99 (profit ~£6.47, 29%) and cost set to £12.49 ex-VAT. **Still wrong: the order alert's Amazon link / supplier_product_id points at the single variant** - needs the correct 2-pack ASIN from the user.
- UPDATE: user confirmed B0BLVT2Y9N IS the 2-pack ASIN (a variation family). Root cause was URL: Amazon /dp/ASIN without `?th=1&psc=1` shows the DEFAULT variation (single torch, £10.99); with it, the page shows the real child (2 Pack, £14.99, verified). All Amazon product URLs in amazon_source (get_product, check_amazon_listing), alerts, orders, stock_monitor, watch_scan, supplier, auto_order, telegram_listener now append `?th=1&psc=1` (backups in server .bak-th1/). Other variation listings may have had mis-read prices/stock before this fix.
- Root cause class: the automatic pipeline (supplier._search_amazon) matches by keyword/price and does not verify pack count / variation. The Telegram flow (`sourcing_job.pick_best`) does check pack size. TODO: audit active listings whose titles say "2 pack / pack of N / N pcs" against the sourced Amazon title; add a pack-count check to the auto pipeline.

## Server housekeeping + listing audit (2026-11-05)
- Disk was 94% full (2.5 GB free). Cause: /opt/applyexpress/.tmp held ~2,400 leftover `indeed_scrape_tmp_*` Chrome profile dirs (8.2 GB, 400k+ files; scrape_indeed.js never cleans them up). Deleted those >1 day old + greenhouse debug PNGs >7 days, vacuumed systemd journal to 150 MB, apt clean. Now 69% used (12 GB free), inodes 57% -> 32%. **Cron 04:00 daily** now removes `indeed_scrape_tmp_*` dirs older than 1 day (nice/ionice). NOT touched: other projects' data (applyexpress/data 4.4 GB, social-media-agent 3 GB, fabdealz-store, interview-owl, kokoro-tts), Playwright browser versions 1208/1228 (other projects use them; ebay agents use 1223).
- RAM is the real constraint (3.8 GB; swap 3 GB full): Medusa (fabdealz store) ~250 MB, n8n ~180 MB, next-server ~130 MB, + Chromium for agents. Run browser jobs one at a time (growth runs use flock /tmp/growth.lock).
- `audit_listings.py`: checks every active Amazon-sourced listing against the live (variation-selected) Amazon page: real profit at listed price, cost drift, pack-count mismatch (eBay title vs Amazon title), out-of-stock. Report only (audit_listings.csv + Telegram digest), changes nothing. Queued 2026-11-05 to start when the growth run releases /tmp/growth.lock; log /tmp/audit_listings.log.

## INCIDENT 2026-11-08/09: missed order + stale Amazon prices - root causes and fixes
Symptoms (user): agent failed to update an Amazon price change (order fulfilled at a loss), failed to notify a new order.
1. **Crash loop 2026-11-08 06:02-23:59 UTC (1,400+ restarts of ebay-agent-2).** Anthropic credits ran out (heavy campaign/growth usage + Telegram bot) -> `_run_startup_research_if_missed()` raised an unhandled `anthropic.BadRequestError` at startup -> process exit -> systemd restart (every ~15 s) -> repeat. The agent never stayed up long enough to run its 15-minute Order Polling job, and the 180-min lookback then missed order **14-15395-52439** (Spear Jackson plane, £19.49, 2026-11-08 19:12 UTC). Recovered 2026-11-09 12:32 UTC.
2. **Amazon price/stock monitoring silently dead since 2026-10-17 (6,000+ "Sync API inside the asyncio loop" warnings).** `amazon_source.close()` did all teardown in ONE try block; after Chrome was OOM-killed, `_context.close()` raised, so `_pw.stop()` never ran, Playwright's loop leaked in that thread, and every later `sync_playwright().start()` failed until the whole agent restarted. Stock Monitor/Replenish then "executed successfully" while checking nothing -> stale prices.
3. **Root pressure: RAM.** 3.8 GB server shared with Medusa/n8n/next.js/other projects' Chromium; swap permanently full; kernel OOM-killed Chrome ~1-4x/day (Nov 2-9), usually at 06:00/18:00 research cycles or during my extra runs.
Fixes deployed 2026-11-09 (backups server `.bak-reliability/`):
- amazon_source: `close()` tears down each step independently; `_get_context()` detects a dead browser (`is_connected()`), resets and restarts it.
- orders: poll window = since last SUCCESSFUL poll (+15 min), 180 min..7 days (`.last_order_poll`; 24h if none); alerts via Telegram after 3 consecutive failed polls; main.py runs an order poll immediately on every start.
- main.py: startup research wrapped in try/except (no more crash loop). lister: Anthropic credit exhaustion = Telegram alert (once/6h) + skip, never crash.
- Forced a full Amazon monitor run (stock_now.py) after the fix; audit_listings.py now saves row-by-row and restarts the browser.
Still open: eBay post-order (cases) API returns 401 (monitor.py "Could not fetch open cases") - token scope needs re-auth; SMTP email alerts broken (Telegram is the channel); CJ trending parse error on price ranges; RAM upgrade recommended; Anthropic auto-reload recommended; consider a watchdog that alerts if the agent restarts >3x/hour.

## Auto-reprice on Amazon price rises (2026-11-09, user request)
- `stock_monitor.run_amazon_monitor` no longer pauses a listing just because Amazon's price rose. `_auto_reprice()`: new eBay price = listed + (Amazon rise) / (1 - fees) [fees = ad rate*1.2 (+15% unless EBAY_NO_SELLER_FEES=1)], so the planned profit is kept; DB `listed_price_gbp` and `cost_gbp` (ex-VAT baseline) are updated; Telegram note "Repriced: ...". Baseline cost only moves UP: if Amazon dips we keep the extra profit; a return to the old price changes nothing.
- Guards (env): `AUTO_REPRICE=1` (0 disables), `AUTO_REPRICE_MAX_GBP=8`, `AUTO_REPRICE_MAX_PCT=25` (ignored when the rise is <= £2): bigger jumps are likelier a wrong variation/spike -> not repriced, Telegram "please check" + old pause logic applies. Out-of-stock still pauses. Unreadable Amazon pages change nothing. `_set_ebay_price()` uses bulk_update_price_quantity (quantity untouched). Monitor Telegram alerts now use the retrying `telegram_bot._send`.
- Not done: lowering prices when Amazon drops (deliberately keeps extra profit); competitor-price ceiling check (repriced items may become uncompetitive).

## Server memory/CPU cleanup (2026-11-09)
- Server = Hetzner, **2 vCPU / 3.8 GB RAM**, swap (3 GB) permanently near full, load avg 7-9 -> Amazon page loads (Playwright, 30 s goto timeout) time out; kernel OOM kills Chrome 1-4x/day. Shared by ~20 services (ebay-agent x2, findall-agent1/2, 7x linkedin-poster-*, saas-bot/web, fabdealz store backend+storefront+bot, n8n, interview-owl, applyexpress...).
- RAM by service (MB): ebay-agent-2 ~540, findall-agent2 ~290, fabdealz-store-backend ~240, n8n ~225, linkedin-poster x7 ~55-127 each (~680 total), findall-agent1 ~100, storefront ~100.
- Done: force-killed 4 wedged Chromium (v1228) belonging to findall agent2 (aged 25-103 h; its job `MessageBus.process_commands` was skipping "maximum running instances" every 30 s) -> freed ~350 MB RAM + ~1 GB swap; agent2 resumed scraping. Removed my temp scripts and rebuildable caches (pip, node-gyp). Earlier: 9.5 GB disk freed (indeed_scrape_tmp).
- Found, NOT touched (other projects): a second `python3 -m agent2.main` started by hand ~89 days ago (pid 3990057, outside systemd) duplicating the findall-agent2 service; findall agent2 leaks browsers (needs `browser.close()` in finally / periodic restart).
- Recommended: upgrade to a 4 vCPU / 8 GB Hetzner plan; or retire unused services (n8n? duplicate agent2?, idle linkedin-poster instances).
