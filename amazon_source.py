"""
Amazon UK as a sourcing channel, driven by a real (headless) browser via Playwright.

No Amazon buying API exists for ordinary buyers, so this module only READS:
  - search_amazon()      → candidates for a keyword (same dict shape as supplier.py)
  - check_amazon_listing() → current price / in-stock state for one ASIN

Ordering is manual: orders for supplier="amazon" send an alert with the ASIN
link + buyer address (see supplier._place_amazon_order).
"""

import atexit
import json
import logging
import queue
import random
import re
import threading
import time
from urllib.parse import quote_plus, urlsplit

log = logging.getLogger(__name__)

_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")

# Quality gates — a bad Amazon source becomes a bad eBay review.
MIN_RATING = 4.0
MIN_REVIEWS = 50
import os as _os
# With Prime, delivery is free; otherwise assume a typical paid delivery charge.
DEFAULT_PAID_DELIVERY_GBP = 0.0 if _os.getenv("AMAZON_FREE_DELIVERY", "0") == "1" else 3.99

_lock = threading.Lock()
_pw = None
_browser = None
_context = None
_blocked_until = 0.0  # backoff after a CAPTCHA / robot check
last_failure = 0.0    # when a browser read last FAILED (a failed read is not "nothing found" - callers can tell them apart)
_last_status = 0      # HTTP status of the page _goto() opened last (404 = the product really is gone)
_CARDS = "div[data-component-type='s-search-result'][data-asin]"
# Amazon's cookies are kept between browser sessions: a visitor with no cookies at all, landing straight on a
# search page, is exactly what Amazon challenges.
_COOKIE_FILE = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), ".amazon_cookies.json")
_COOKIE_MAX_AGE = 20 * 3600

# Where Amazon is told to deliver. This server sits in a datacentre in Finland, and a visitor Amazon places in Finland
# is shown EXPORT terms: most search results lose their price ("See options" - and a card without a price is skipped),
# delivery is charged per item and takes weeks, and whatever cannot be exported reads "cannot be dispatched to your
# selected delivery location" with no Add to Basket button - which the stock check took for OUT OF STOCK and paused
# the listing. So every browser session is given a UK postcode, and a page showing any other delivery place is a
# FAILED read: never a price, a stock level or "nothing found".
AMAZON_POSTCODE = (_os.getenv("AMAZON_POSTCODE") or "M1 1AE").strip().upper()
_located = False            # this browser session has been seen delivering to AMAZON_POSTCODE
location_failed = ""        # why the UK delivery location could not be set (empty = no problem); shown in alerts
# zero-width characters Amazon pads its header text with (they are not whitespace to str.split)
_INVISIBLE = dict.fromkeys((0x200B, 0x200C, 0x200D, 0x200E, 0x200F, 0x2060, 0xFEFF))

# ---------------------------------------------------------------------------
# One browser thread.
# Playwright's sync API belongs to the thread that started it, but the agent's scheduler runs its jobs on a pool of
# threads. A browser opened by one job was therefore used and closed by another job's thread: the close failed
# silently, Chromium was left running (memory climbed until the service was OOM-killed) and the thread that had opened
# it could never start a browser again ("Sync API inside the asyncio loop") - price monitoring and Amazon matching
# then failed for hours while reporting "nothing found". Every browser operation now runs on ONE dedicated thread.
# ---------------------------------------------------------------------------
CALL_TIMEOUT = 300
_jobs: "queue.Queue" = queue.Queue()
_bthread = None
_bthread_guard = threading.Lock()


def _browser_loop():
    while True:
        fn, args, kwargs, box, done = _jobs.get()
        try:
            if not box.get("abandoned"):         # the caller gave up waiting: do not do the work for nobody
                box["value"] = fn(*args, **kwargs)
        except BaseException as e:               # hand every failure back to the caller; this thread must not die
            box["error"] = e
        finally:
            done.set()


def _submit(fn, args, kwargs, timeout):
    global _bthread
    if threading.current_thread() is _bthread:
        return fn(*args, **kwargs)
    with _bthread_guard:
        if _bthread is None or not _bthread.is_alive():
            _bthread = threading.Thread(target=_browser_loop, name="amazon-browser", daemon=True)
            _bthread.start()
    box, done = {}, threading.Event()
    _jobs.put((fn, args, kwargs, box, done))
    if not done.wait(timeout):
        box["abandoned"] = True
        raise TimeoutError(f"browser call {getattr(fn, '__name__', fn)} not finished after {timeout}s")
    if "error" in box:
        raise box["error"]
    return box.get("value")


def on_browser(fn, *args, **kwargs):
    """Run fn(*args, **kwargs) on the browser thread and return its result (its exception is re-raised here).
    Anything that touches _get_context() / a page must go through this."""
    return _submit(fn, args, kwargs, CALL_TIMEOUT)


def _failed():
    global last_failure
    last_failure = time.time()


def _get_context():
    global _pw, _browser, _context
    if threading.current_thread() is not _bthread:
        raise RuntimeError("browser code must run on the browser thread - wrap it in amazon_source.on_browser(fn)")
    if _context:
        try:
            alive = _browser is not None and _browser.is_connected()
        except Exception:
            alive = False
        if alive:
            return _context
        log.warning("Amazon browser is dead (probably OOM-killed) - restarting it")
        close()                      # full reset so a fresh browser can start
    elif _pw:
        close()                      # leaked Playwright from an earlier failure
    from playwright.sync_api import sync_playwright
    _pw = sync_playwright().start()
    _browser = _pw.chromium.launch(headless=True, args=["--no-sandbox", "--disable-dev-shm-usage"])
    _context = _browser.new_context(
        user_agent=_UA, locale="en-GB", timezone_id="Europe/London",
        viewport={"width": 1366, "height": 900},
    )
    try:
        if time.time() - _os.path.getmtime(_COOKIE_FILE) < _COOKIE_MAX_AGE:
            with open(_COOKIE_FILE, encoding="utf-8") as f:
                _context.add_cookies(json.load(f))
    except Exception:
        pass                         # no saved cookies, or an unreadable file: start as a new visitor
    # The server's datacentre IP is not in the UK; pin currency/language so prices come back in GBP.
    _context.add_cookies([
        {"name": "i18n-prefs", "value": "GBP", "domain": ".amazon.co.uk", "path": "/"},
        {"name": "lc-acbuk", "value": "en_GB", "domain": ".amazon.co.uk", "path": "/"},
    ])
    # Images/fonts/media aren't needed to read text; saves memory and bandwidth.
    _context.route(
        "**/*",
        lambda r: r.abort() if r.request.resource_type in ("font", "media") else r.continue_(),
    )
    return _context


def _close():
    """Tear everything down (browser thread only). Each step is independent: a crashed browser makes
    context/browser close() raise, and skipping _pw.stop() would leave Playwright's event loop running."""
    global _pw, _browser, _context, _located
    _save_cookies()
    for step in (lambda: _context and _context.close(), lambda: _browser and _browser.close(),
                 lambda: _pw and _pw.stop()):
        try:
            step()
        except Exception:
            pass
    _pw = _browser = _context = None
    _located = False                 # the next session proves its delivery location afresh


def close():
    """Close the browser (from any thread). The next call starts a fresh one."""
    if _bthread is None or not _bthread.is_alive():
        return                                   # never started in this process
    try:
        _submit(_close, (), {}, 60)
    except Exception:
        pass


atexit.register(close)


def _is_blocked(page) -> bool:
    try:
        t = page.title().lower()
        if "robot check" in t or "sorry" in t:
            return True
        return page.locator("form[action*='validateCaptcha'], #captchacharacters").count() > 0
    except Exception:
        return False


def _save_cookies():
    """Keep Amazon's cookies for the next browser session (browser thread only). Never raises."""
    try:
        if not _context or time.time() < _blocked_until:
            return                               # a session Amazon has just blocked is not worth keeping
        cookies = [c for c in _context.cookies() if "amazon" in c.get("domain", "")]
        tmp = f"{_COOKIE_FILE}.{_os.getpid()}.tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(cookies, f)
        _os.replace(tmp, _COOKIE_FILE)
    except Exception:
        pass


def _forget_cookies():
    try:
        _os.remove(_COOKIE_FILE)
    except OSError:
        pass


def _wait_out_challenge(page, patience: float) -> bool:
    """Amazon's browser challenge (HTTP 202, header x-amzn-waf-action: challenge) is an EMPTY page whose script
    works out a token, stores it in the aws-waf-token cookie and reloads itself into the real page - 6 to 15 s on
    this server. Read before that, it looks exactly like a search with no results. True once the real page is there."""
    end = time.time() + patience
    while time.time() < end:
        time.sleep(1.5)
        try:
            if page.evaluate("typeof window.gokuProps === 'undefined' && document.readyState !== 'loading'"
                             " && document.title.length > 0"):
                return True
        except Exception:
            pass                                 # in the middle of the reload
    return False


def _goto(page, url: str) -> bool:
    """Open url. False means Amazon would not show the page (robot check, or a challenge that did not clear):
    that is a FAILED read, never 'nothing there'."""
    global _blocked_until, _last_status
    docs = []                                    # (status, challenge type) of each main-document response, newest last

    def note(r):
        try:
            if r.request.is_navigation_request() and r.frame == page.main_frame:
                docs.append((r.status, (r.headers.get("x-amzn-waf-action") or "").lower()))
        except Exception:
            pass

    def challenged():
        status, action = docs[-1] if docs else (0, "")
        return action == "challenge" or status == 202

    page.on("response", note)
    for attempt in (1, 2):
        seen = len(docs)
        resp = page.goto(url, wait_until="domcontentloaded", timeout=30000)
        if resp is not None and len(docs) == seen:
            docs.append((resp.status, (resp.headers.get("x-amzn-waf-action") or "").lower()))
        if challenged():
            cleared = _wait_out_challenge(page, 30)
            if not cleared:
                try:
                    page.reload(wait_until="domcontentloaded", timeout=30000)
                except Exception:
                    pass
                cleared = _wait_out_challenge(page, 25) if challenged() else True
            if not cleared:
                _failed()
                _blocked_until = time.time() + 10 * 60
                _forget_cookies()
                log.warning("Amazon's browser challenge did not clear - pausing Amazon browsing for 10 min")
                return False
            log.info("Amazon browser challenge passed")
            _save_cookies()                      # the token just earned spares the next session the same wait
        _last_status, action = docs[-1] if docs else (0, "")
        try:  # UK cookie banner
            page.locator("#sp-cc-accept").click(timeout=1500)
        except Exception:
            pass
        if not (action == "captcha" or _last_status == 405 or _is_blocked(page)):
            return True
        what = _describe(page)
        if attempt == 1 and _press_continue(page):
            _save_cookies()
            if _same_page(page.url, url):
                _last_status = docs[-1][0] if docs else _last_status
                log.info(f"Amazon asked us to confirm we are a visitor - pressed 'Continue shopping' and carried on ({what})")
                return True
            # the button leads to Amazon's HOME page, not to the page we asked for: ask for that page again
            log.info(f"Amazon asked us to confirm we are a visitor - pressed 'Continue shopping', opening the page again ({what})")
            time.sleep(random.uniform(1.5, 3.0))
            continue
        _blocked_until = time.time() + 30 * 60
        _forget_cookies()
        log.warning(f"Amazon served a robot check — pausing Amazon browsing for 30 min ({what})")
        return False
    return True


def _delivery_place(page) -> str:
    """Where Amazon says it delivers for this visitor: the 'Deliver to ...' line in the page header ('Deliver to
    Finland', 'Deliver to Manchester M11AE'). Empty when the page states no place: Amazon's 'not found' page has no
    header, and about one product page in twenty arrives with the bare label 'Deliver to' and nothing after it
    (taking that for 'another place' sent the price check to the home page 9 times in 171 listings)."""
    out = []
    for sel in ("#glow-ingress-line1", "#glow-ingress-line2"):
        try:
            loc = page.locator(sel)
            if loc.count():
                text = loc.first.inner_text(timeout=2000).translate(_INVISIBLE)
                out.append(" ".join(text.split()))
        except Exception:
            pass
    place = " ".join(x for x in out if x)
    return "" if place.lower().rstrip(": ") in ("deliver to", "delivering to") else place


def _place_when_ready(page, patience: float = 5.0) -> str:
    """_delivery_place(), giving a header that so far shows only its label a few seconds to fill the place in."""
    end = time.time() + patience
    place = _delivery_place(page)
    while not place and time.time() < end:
        time.sleep(1.0)
        place = _delivery_place(page)
    return place


def busy_elsewhere() -> bool:
    """Is ANOTHER process of this agent using a browser right now (the daily growth run, a price pass started by
    hand)? Those hold /tmp/growth.lock for as long as they run. This server has memory for one browser at a time:
    a second one got the first killed half-way through a price check."""
    try:
        import fcntl
    except ImportError:                                    # not Linux (a developer's machine)
        return False
    try:
        with open("/tmp/growth.lock", "a") as fh:
            try:
                fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
                fcntl.flock(fh, fcntl.LOCK_UN)
                return False
            except OSError:
                return True
    except OSError:
        return False


def _postcode_shown(place: str) -> bool:
    """Is our UK postcode what Amazon delivers to? The header shows it as 'M11AE'; some layouts drop the last two
    letters, so the outward code and the digit after it are what is looked for."""
    want = re.sub(r"[^A-Z0-9]", "", AMAZON_POSTCODE)
    return len(want) >= 5 and want[:-2] in re.sub(r"[^A-Z0-9]", "", place.upper())


def _choose_postcode(page) -> bool:
    """Give this browser session the UK delivery postcode through the 'Deliver to' box in Amazon's page header, the
    way a shopper does (browser thread only). Amazon keeps the choice with the session, so the saved cookies carry
    it into later sessions. True once the header shows the postcode."""
    global _located, _blocked_until, location_failed
    step = "opening Amazon's home page"
    try:
        if not _goto(page, "https://www.amazon.co.uk/"):
            return False                                   # blocked: _goto has already said so and set the pause
        was = _place_when_ready(page)
        if _postcode_shown(was):
            _located, location_failed = True, ""
            return True
        box = page.locator("#GLUXZipUpdateInput")
        for attempt in (1, 2):                             # the header's script may not be listening yet: press twice
            step = "opening the 'Deliver to' box"
            page.locator("#nav-global-location-popover-link").first.click(timeout=15000)
            try:
                box.first.wait_for(state="visible", timeout=8000)
                break
            except Exception:
                change = page.locator("#GLUXChangePostalCodeLink:visible")
                if change.count():                         # a postcode is already chosen: the box is behind 'Change'
                    change.first.click(timeout=5000)
                    box.first.wait_for(state="visible", timeout=8000)
                    break
                if attempt == 2:
                    raise
                time.sleep(2)
        step = f"entering the postcode {AMAZON_POSTCODE}"
        box.first.fill(AMAZON_POSTCODE)
        page.locator("#GLUXZipUpdate").first.click(timeout=10000)
        done = page.locator("#GLUXConfirmClose:visible, input[name='glowDoneButton']:visible, "
                            "button[name='glowDoneButton']:visible")
        try:
            done.first.wait_for(state="visible", timeout=8000)
            done.first.click(timeout=5000)
        except Exception:
            pass                                           # some layouts apply the postcode without a second button
        time.sleep(3)                                      # Amazon reloads the page with the new location
        step = "re-opening Amazon's home page"
        if not _goto(page, "https://www.amazon.co.uk/"):
            return False
        now = _place_when_ready(page, 8.0)
        if _postcode_shown(now):
            _located, location_failed = True, ""
            log.info(f"Amazon delivery location set: '{was}' -> '{now}'")
            _save_cookies()
            return True
        raise RuntimeError(f"the header still says '{now}'")
    except Exception as e:
        # Handled like a block: callers keep their work for later instead of reading export prices, and the price
        # monitor waits and then says so in its alert.
        _blocked_until = max(_blocked_until, time.time() + 10 * 60)
        location_failed = (f"Amazon would not take the UK delivery postcode {AMAZON_POSTCODE} "
                           f"({step}: {str(e).splitlines()[0][:120]})")
        log.error(f"{location_failed} - Amazon pages are NOT being read until it does; next attempt in 10 min")
        return False


def _open(page, url: str) -> bool:
    """_goto() plus the guarantee that Amazon is showing UK delivery. False = a FAILED read (blocked, or the UK
    delivery location could not be set) - never 'nothing there'."""
    global _located
    if not _goto(page, url):
        return False
    place = _delivery_place(page) if _located else _place_when_ready(page, 4.0)
    if _postcode_shown(place):
        _located = True
        return True
    if not place and _located:
        return True                                        # a page that states no place, in a session already proven
    log.info(f"Amazon is showing delivery to '{place or 'nowhere stated'}' - choosing the UK postcode {AMAZON_POSTCODE}")
    if not _choose_postcode(page):
        _failed()
        return False
    if not _goto(page, url):
        return False
    place = _delivery_place(page)
    if place and not _postcode_shown(place):
        _failed()
        log.warning(f"Amazon page still shows delivery to '{place}' after the postcode was chosen - read failed")
        return False
    return True


def _same_page(now: str, asked: str) -> bool:
    """Did the browser end up on the page that was asked for (same path), rather than somewhere else on Amazon?"""
    try:
        a, b = urlsplit(now), urlsplit(asked)
        return a.path.rstrip("/") == b.path.rstrip("/") and (b.path.rstrip("/") != "/s" or a.query == b.query)
    except Exception:
        return False


def _describe(page) -> str:
    """What Amazon put in front of us, for the log - so the next block can be understood instead of guessed at."""
    try:
        text = page.evaluate("document.body ? document.body.innerText.slice(0, 140) : ''").replace("\n", " ")
        puzzle = "yes" if page.locator("#captchacharacters").count() else "no"
        return f"HTTP {_last_status}, title {page.title()[:50]!r}, puzzle {puzzle}, text {text!r}"
    except Exception:
        return f"HTTP {_last_status}"


def _press_continue(page) -> bool:
    """Amazon's milder check is a page with one 'Continue shopping' button and no puzzle: press it, as the page asks
    every visitor to. A character puzzle is left alone - that is a real block, and the answer to it is to back off."""
    try:
        if page.locator("#captchacharacters").count():
            return False
        btn = page.locator("form[action*='validateCaptcha'] button[type='submit'], "
                           "form[action*='validateCaptcha'] input[type='submit']")
        if btn.count() == 0:
            return False
        with page.expect_navigation(wait_until="domcontentloaded", timeout=30000):
            btn.first.click(timeout=5000)
        time.sleep(2)
        return not _is_blocked(page)
    except Exception:
        return False


def _has_product(page) -> bool:
    """A product page that has really arrived carries #productTitle; give a slow page a moment before deciding."""
    try:
        page.wait_for_selector("#productTitle", state="attached", timeout=8000)
        return True
    except Exception:
        return False


# Pages that arrive without their product. On the night of 12-13 Nov a third of all product pages came back like
# that (right <title>, the navigation bar, no product), a different third each time - so the fault is in the
# delivery of the page, not in particular products, and asking again is the cure. Each one was a price left unchecked.
_EVIDENCE_DIR = "/tmp/ea2-pages"
_evidence = 0                              # incomplete pages this process has kept on disk
_second = {"tried": 0, "worked": 0}        # second attempts at an incomplete page, and how many brought the product

_PAGE_FACTS = """() => {
    const n = performance.getEntriesByType('navigation')[0] || {};
    const q = s => !!document.querySelector(s);
    const kids = document.body ? Array.from(document.body.children) : [];
    return {
        ready: document.readyState, nodes: document.getElementsByTagName('*').length,
        html_bytes: n.decodedBodySize || 0,
        waited_ms: Math.round((n.responseStart || 0) - (n.requestStart || 0)),
        download_ms: Math.round((n.responseEnd || 0) - (n.responseStart || 0)),
        dp: q('#dp'), ppd: q('#ppd'), centre: q('#centerCol'), title_box: q('#title, #titleSection'),
        buy: q('#add-to-cart-button, #buy-now-button'), footer: q('#navFooter'),
        h1: ((document.querySelector('h1') || {}).innerText || '').trim().slice(0, 50),
        last: kids.slice(-3).map(e => e.tagName.toLowerCase() + (e.id ? '#' + e.id : '')).join(' ')
    };
}"""


def _incomplete(page, asin: str) -> str:
    """What exactly arrived, for the log - and the page itself kept on disk for the first few, so that the cause can
    be read off instead of guessed at. Never raises."""
    global _evidence
    try:
        facts = page.evaluate(_PAGE_FACTS)
    except Exception as e:
        facts = {"unreadable": str(e).splitlines()[0][:100]}
    try:
        if _evidence < 12:
            _os.makedirs(_EVIDENCE_DIR, exist_ok=True)
            with open(_os.path.join(_EVIDENCE_DIR, f"{asin}-{time.strftime('%H%M%S')}.html"), "w",
                      encoding="utf-8") as f:
                f.write(page.content())
            _evidence += 1
    except Exception:
        pass
    return ", ".join(f"{k} {v!r}" if isinstance(v, str) else f"{k} {v}" for k, v in facts.items())


def _arrived(page, url: str, asin: str) -> bool:
    """Is the product really on the page? A page that came without it is described in the log and asked for ONCE
    more after a pause - for as long as asking again is seen to work in this process (if it stops working, the cause
    is something a second request does not cure, and doubling the traffic to Amazon would only make things worse).
    False leaves the caller where it was before: _gone() tells 'no such product' from 'not read'."""
    if _has_product(page):
        return True
    if _gone(page):
        return False
    log.warning(f"Amazon page for {asin} arrived without its product ({_incomplete(page, asin)})")
    if _second["tried"] >= 8 and _second["worked"] * 4 < _second["tried"]:
        return False
    _second["tried"] += 1
    time.sleep(random.uniform(4.0, 7.0))
    if not _goto(page, url):
        return False
    time.sleep(random.uniform(1.0, 2.0))
    if not _has_product(page):
        return False
    _second["worked"] += 1
    log.info(f"Amazon page for {asin} arrived complete on the second try "
             f"({_second['worked']} of {_second['tried']} second tries have worked)")
    return True


def _gone(page) -> bool:
    """Amazon's own 'page not found': the product no longer exists (as opposed to a page that failed to load)."""
    try:
        return _last_status == 404 or "page not found" in page.title().lower()
    except Exception:
        return _last_status == 404


def _avail_text(page) -> str:
    """Availability blurb; tolerant of pages with several/odd #availability nodes."""
    try:
        return page.locator("#availability").first.inner_text(timeout=2000).lower()
    except Exception:
        return ""


_MONTHS = {m: i for i, m in enumerate(["january", "february", "march", "april", "may", "june", "july", "august",
                                       "september", "october", "november", "december"], 1)}
_DELIVERY_BOXES = ("#mir-layout-DELIVERY_BLOCK-slot-PRIMARY_DELIVERY_MESSAGE_LARGE", "#deliveryBlockMessage",
                   "#mir-layout-DELIVERY_BLOCK", "#delivery-block-ags-dcp-feature-div", "#ddmDeliveryMessage")


def parse_delivery_date(text: str, today=None):
    """The first delivery date in Amazon's delivery blurb ('FREE delivery Thursday, 8 January', '18 - 20 November',
    'tomorrow'), as a date; None when the text names none. A range counts as its LAST day."""
    import datetime as _dt
    today = today or _dt.date.today()
    low = (text or "").lower()
    if not low.strip():
        return None
    dates = []
    for m in re.finditer(r"(\d{1,2})(?:st|nd|rd|th)?\s*(?:-|–|to)?\s*(\d{1,2})?(?:st|nd|rd|th)?\s+(" + "|".join(_MONTHS) + r")\b", low):
        day = int(m.group(2) or m.group(1))
        month = _MONTHS[m.group(3)]
        for year in (today.year, today.year + 1):
            try:
                d = _dt.date(year, month, day)
            except ValueError:
                break
            if d >= today - _dt.timedelta(days=30):
                dates.append(d)
                break
        if dates:
            break
    if dates:
        return dates[0]
    if re.search(r"\btomorrow\b", low):
        return today + _dt.timedelta(days=1)
    if re.search(r"\btoday\b", low):
        return today
    return None


def _delivery_estimate(page) -> dict:
    """What Amazon promises THIS product page for the UK delivery postcode: {'text', 'date' (iso or ''), 'days'}.
    days is None when the page names no date (the caller must not treat that as 'fast')."""
    import datetime as _dt
    text = ""
    for sel in _DELIVERY_BOXES:
        try:
            loc = page.locator(sel)
            if loc.count():
                text = re.sub(r"\s+", " ", loc.first.inner_text(timeout=1500)).strip()
                if text:
                    break
        except Exception:
            continue
    d = parse_delivery_date(text)
    return {"text": text[:160], "date": d.isoformat() if d else "",
            "days": (d - _dt.date.today()).days if d else None}


def _price(text: str | None) -> float | None:
    if not text:
        return None
    m = re.search(r"£\s*([\d,]+(?:\.\d{1,2})?)", text)
    return float(m.group(1).replace(",", "")) if m else None


def _hires(img_url: str) -> str:
    """Amazon thumbnails carry a size token (._AC_UY218_.) — strip it for the full image."""
    return re.sub(r"\._[A-Za-z0-9_,%-]+_\.", ".", img_url or "")


def search_amazon(keyword: str, max_price_gbp: float, limit: int = 4, strict: bool = True,
                  min_reviews: int | None = None) -> list[dict]:
    """
    Search amazon.co.uk and return up to `limit` candidates in supplier.py's shape.
    Skips sponsored cards, low-rated / low-review products and anything over max_price_gbp.
    An empty list with amazon_source.last_failure set during the call means the read FAILED, not "no results".
    """
    try:
        return on_browser(_search_amazon, keyword, max_price_gbp, limit, strict, min_reviews)
    except Exception as e:
        _failed()
        log.warning(f"Amazon search failed for '{keyword}': {e}")
        return []


def check_amazon_listing(asin: str) -> dict:
    """
    Open the product page. Returns {"found": bool, "in_stock": bool|None, "price": float|None,
    "blocked": bool}. in_stock is None when the page could not be read.
    """
    try:
        return on_browser(_check_amazon_listing, asin)
    except Exception as e:
        _failed()
        log.warning(f"Amazon check failed for {asin}: {e}")
        return {"found": False, "in_stock": None, "price": None, "blocked": False}


def get_product(asin: str) -> dict | None:
    """
    Full product-page read for one ASIN (used when a product is supplied by hand).
    Returns a supplier-style dict (cost_gbp is the VAT-inclusive shelf price), or None.
    """
    try:
        return on_browser(_get_product, asin)
    except Exception as e:
        _failed()
        log.warning(f"Amazon product read failed for {asin}: {e}")
        return None


def _search_amazon(keyword: str, max_price_gbp: float, limit: int = 4, strict: bool = True,
                   min_reviews: int | None = None) -> list[dict]:
    if time.time() < _blocked_until:
        return []
    with _lock:
        page = None
        try:
            page = _get_context().new_page()
            if not _open(page, f"https://www.amazon.co.uk/s?k={quote_plus(keyword)}"):
                return []
            try:
                page.wait_for_selector(_CARDS, state="attached", timeout=10000)
            except Exception:
                # No result cards. A genuine "no results" page still has the search layout; a page that never
                # arrived has not - and that is a failed read, which must not be reported as "Amazon has nothing".
                if page.locator("div.s-main-slot, #search").count() == 0:
                    _failed()
                    log.warning(f"Amazon search page for '{keyword}' did not load (HTTP {_last_status}) - read failed")
                    return []
            time.sleep(random.uniform(1.0, 2.0))
            cards = page.locator(_CARDS).all()
            out = []
            skipped = {}                         # why cards were passed over - so an empty result can be understood

            def skip(why):
                skipped[why] = skipped.get(why, 0) + 1

            for c in cards[:24]:
                asin = (c.get_attribute("data-asin") or "").strip()
                if not asin:
                    continue
                text = c.inner_text()
                if re.search(r"\bSponsored\b", text):
                    skip("adverts")
                    continue
                price = _price(c.locator(".a-price .a-offscreen").first.text_content()
                               if c.locator(".a-price .a-offscreen").count() else None)
                if not price:
                    skip("no price shown")
                    continue
                if price > max_price_gbp:
                    skip(f"over GBP {max_price_gbp:.2f}")
                    continue
                rating, reviews = 0.0, 0
                rl = c.locator("[aria-label*='out of 5 stars']")
                if rl.count():
                    m = re.search(r"([\d.]+) out of 5", rl.first.get_attribute("aria-label") or "")
                    rating = float(m.group(1)) if m else 0.0
                rv = c.locator("a[href*='#customerReviews'] span, span.s-underline-text")
                if rv.count():
                    m = re.search(r"([\d.,]+)\s*([KkMm]?)", rv.first.inner_text())
                    if m:
                        n = float(m.group(1).replace(",", ""))
                        reviews = int(n * {"": 1, "k": 1000, "m": 1000000}[m.group(2).lower()])
                if rating < MIN_RATING or reviews < (MIN_REVIEWS if min_reviews is None else min_reviews):
                    skip("rating/reviews too low")
                    continue
                # Some card layouts put only the brand in the first <h2>; take the longest of the h2 texts
                # and the product image's alt text, which carries the full title.
                texts = []
                for h2 in c.locator("h2").all():
                    try:
                        texts.append(h2.inner_text().strip())
                    except Exception:
                        pass
                alt = c.locator("img.s-image")
                if alt.count():
                    texts.append((alt.first.get_attribute("alt") or "").strip())
                title = max(texts, key=len) if texts else keyword
                # Relevance: every meaningful keyword word (matched on its first 5 letters) must
                # appear in the title, otherwise "electric planer" matches a manual hand plane.
                words = [w[:5] for w in re.findall(r"[a-z0-9]+", keyword.lower())
                         if len(w) >= 4 and w not in ("with", "for", "and", "the")]
                if strict and any(w not in title.lower() for w in words):
                    skip("not the product searched for")
                    continue
                img_el = c.locator("img.s-image")
                img = _hires(img_el.first.get_attribute("src")) if img_el.count() else ""
                prime = c.locator("i.a-icon-prime").count() > 0
                free_delivery = bool(re.search(r"FREE\s+(?:UK\s+)?[Dd]elivery", text)) or prime
                out.append({
                    "supplier": "amazon",
                    "product_id": asin,
                    "title": title,
                    "cost_gbp": price,
                    "shipping_gbp": 0.0 if free_delivery else DEFAULT_PAID_DELIVERY_GBP,
                    "delivery_days": 3 if prime else 5,
                    "images": [img] if img else [],
                    "description": "",
                    "uk_warehouse": True,
                    "amazon_rating": rating,
                    "amazon_reviews": reviews,
                })
                if len(out) >= limit:
                    break
            passed = ", ".join(f"{n} {why}" for why, n in skipped.items())
            log.info(f"Amazon: {len(out)} candidate(s) for '{keyword}'" + (f" (passed over: {passed})" if passed else ""))
            return out
        except Exception as e:
            _failed()
            log.warning(f"Amazon search failed for '{keyword}': {e}")
            close()  # drop a possibly wedged browser; next call restarts it
            return []
        finally:
            try:
                if page:
                    page.close()
            except Exception:
                pass


def _check_amazon_listing(asin: str) -> dict:
    if time.time() < _blocked_until:
        return {"found": False, "in_stock": None, "price": None, "blocked": True}
    with _lock:
        page = None
        try:
            page = _get_context().new_page()
            url = f"https://www.amazon.co.uk/dp/{asin}?th=1&psc=1"
            if not _open(page, url):
                return {"found": False, "in_stock": None, "price": None, "blocked": True}
            time.sleep(random.uniform(1.0, 2.0))
            if not _arrived(page, url, asin):
                if _gone(page):                  # Amazon says the product no longer exists
                    return {"found": False, "in_stock": False, "price": None, "blocked": False}
                # Anything else is a page that did not load. Reporting it as "out of stock" would pause a live listing.
                _failed()
                log.warning(f"Amazon page for {asin} came back without a product ({_describe(page)}) - not read")
                return {"found": False, "in_stock": None, "price": None, "blocked": False}
            can_buy = page.locator("#add-to-cart-button, #buy-now-button").count() > 0
            # The product's own price block first. A price from anywhere else on the page is believed only when the
            # page can be bought from: without a buy box, the first price on the page belongs to another variation or
            # to a product in a carousel (an ornament that costs GBP 11.95 was recorded at GBP 599).
            price = None
            for sel in ("#corePrice_feature_div .a-offscreen", "#corePriceDisplay_desktop_feature_div .a-offscreen",
                        ".a-price .a-offscreen")[:3 if can_buy else 2]:
                loc = page.locator(sel)
                if loc.count():
                    price = _price(loc.first.text_content())
                    if price:
                        break
            avail = _avail_text(page)
            in_stock = bool(can_buy and "unavailable" not in avail and price)
            max_qty = None
            m = re.search(r"only\s+(\d+)\s+left", avail)
            if m:
                max_qty = int(m.group(1))
            else:
                try:
                    opts = page.locator("select#quantity option").count()
                    if opts:
                        max_qty = opts
                except Exception:
                    pass
            return {"found": True, "in_stock": in_stock, "price": price, "blocked": False, "max_qty": max_qty}
        except Exception as e:
            _failed()
            log.warning(f"Amazon check failed for {asin}: {e}")
            close()
            return {"found": False, "in_stock": None, "price": None, "blocked": False}
        finally:
            try:
                if page:
                    page.close()
            except Exception:
                pass


def _get_product(asin: str) -> dict | None:
    if time.time() < _blocked_until:
        return None
    with _lock:
        page = None
        try:
            page = _get_context().new_page()
            url = f"https://www.amazon.co.uk/dp/{asin}?th=1&psc=1"
            if not _open(page, url):
                return None
            time.sleep(random.uniform(1.0, 2.0))
            if not _arrived(page, url, asin):
                if not _gone(page):              # the page did not load: a failed read, not "no such product"
                    _failed()
                    log.warning(f"Amazon page for {asin} came back without a product ({_describe(page)}) - not read")
                return None
            title = page.locator("span#productTitle").first.inner_text().strip()
            price = None
            for sel in ("#corePrice_feature_div .a-offscreen", "#corePriceDisplay_desktop_feature_div .a-offscreen",
                        ".a-price .a-offscreen"):
                loc = page.locator(sel)
                if loc.count():
                    price = _price(loc.first.text_content())
                    if price:
                        break
            images = []
            if page.locator("#landingImage").count():
                import json as _json
                try:
                    dyn = _json.loads(page.locator("#landingImage").first.get_attribute("data-a-dynamic-image") or "{}")
                    images = sorted(dyn, key=lambda u: -max(dyn[u]))[:1]
                except Exception:
                    pass
            for t in page.locator("#altImages img").all()[:8]:
                src = _hires(t.get_attribute("src") or "")
                if src and "sprite" not in src and "play-icon" not in src and src not in images:
                    images.append(src)
            # Photos of the SELECTED variant only (landing image + its own thumbnails), upgraded to full size by
            # image id. Never take images from elsewhere in the page source: they belong to other sizes/colours.
            def _img_id(u):
                m = re.search(r"/images/I/([A-Za-z0-9+%-]+?)[._]", u or "")
                return m.group(1) if m else None
            ids = []
            for u in images:
                i = _img_id(u)
                if i and i not in ids:
                    ids.append(i)
            images = [f"https://m.media-amazon.com/images/I/{i}._AC_SL1500_.jpg" for i in ids][:8]
            bullets = [b.inner_text().strip() for b in page.locator("#feature-bullets li").all()[:8]]
            aspects = {}
            try:
                by = page.locator("#bylineInfo").first.inner_text(timeout=1500).strip()
                brand = re.sub(r"^(Visit the |Brand:\s*)", "", by)
                brand = re.sub(r"\s+Store$", "", brand).strip()
                if brand and len(brand) < 40:
                    aspects["Brand"] = [brand]
            except Exception:
                pass
            try:
                row = page.locator("tr:has(th:has-text('Item model number')) td, "
                                   "li:has(span:has-text('Item model number')) span").last
                model = row.inner_text(timeout=1500).strip()
                if model and len(model) < 40:
                    aspects["Model"] = [model]
            except Exception:
                pass
            avail = _avail_text(page)
            in_stock = page.locator("#add-to-cart-button, #buy-now-button").count() > 0 and "unavailable" not in avail
            prime = page.locator("i.a-icon-prime").count() > 0
            dl = _delivery_estimate(page)                # the date Amazon actually promises, not a guess from a badge
            # Delivery cost (owner, 23 Nov 2026): the agent's browser is not signed in to Prime, so the badge is often missing even
            # when the page says FREE delivery (the owner has Prime). Read the delivery line instead of assuming GBP 3.99.
            _dtxt = dl.get("text") or ""
            _fee = re.search(r"[\u00a3]\s?(\d+(?:\.\d\d)?)\s+(?:delivery|postage|shipping)", _dtxt, re.I)
            if prime or re.search(r"FREE\s+(?:UK\s+)?[Dd]elivery", _dtxt):
                _ship = 0.0
            elif _fee:
                _ship = float(_fee.group(1))
            else:
                _ship = DEFAULT_PAID_DELIVERY_GBP
            return {
                "supplier": "amazon", "product_id": asin, "title": title,
                "cost_gbp": price or 0.0, "shipping_gbp": _ship,
                "delivery_days": dl["days"] if dl["days"] is not None else (3 if prime else 5), "images": images[:8],
                "description": " ".join(bullets), "uk_warehouse": True, "in_stock": in_stock,
                "aspects": aspects, "prime": prime, "delivery_text": dl["text"], "delivery_date": dl["date"],
            }
        except Exception as e:
            _failed()
            log.warning(f"Amazon product read failed for {asin}: {e}")
            close()
            return None
        finally:
            try:
                if page:
                    page.close()
            except Exception:
                pass


if __name__ == "__main__":
    import json
    import sys
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    kw = " ".join(sys.argv[1:]) or "laser measure digital"
    res = search_amazon(kw, 80)
    print(json.dumps(res, indent=1)[:3000])
    if res:
        print(check_amazon_listing(res[0]["product_id"]))
    close()
