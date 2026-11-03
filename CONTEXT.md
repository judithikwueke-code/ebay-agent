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
