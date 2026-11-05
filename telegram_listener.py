"""
Telegram front-end for sourcing + listing.

  - Send a SCREENSHOT of a product (caption optional, e.g. "cat=garden_outdoor") or an Amazon UK link/ASIN.
  - The bot reads it, finds the exact product on Amazon UK, works out the eBay price and replies with a PREVIEW
    and buttons.  Nothing is listed until you press "List it".

Light process: long-polls Telegram, runs heavy work (Claude vision, Playwright) in `sourcing_job.py` subprocesses
under a memory-limited systemd scope.  Only the chat in TELEGRAM_CHAT_ID is obeyed.
"""
import json
import logging
import os
import re
import subprocess
import threading
import time
import uuid

import requests
from dotenv import load_dotenv

load_dotenv()
TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CHAT_ID = str(os.getenv("TELEGRAM_CHAT_ID"))
API = f"https://api.telegram.org/bot{TOKEN}"
HERE = os.path.dirname(os.path.abspath(__file__))
PY = os.getenv("JOB_PYTHON", "/var/www/ebay-agent/venv/bin/python")
PENDING_FILE = os.path.join(HERE, "telegram_pending.json")
WORK = os.path.join(HERE, "telegram_jobs")
os.makedirs(WORK, exist_ok=True)
log = logging.getLogger("telegram_listener")
_job_lock = threading.Lock()          # one browser job at a time (server is memory tight)
_pending_lock = threading.Lock()


# ---------------------------------------------------------------- Telegram helpers
def tg(method: str, **payload):
    try:
        r = requests.post(f"{API}/{method}", json=payload, timeout=40)
        return r.json()
    except Exception as e:
        log.warning(f"telegram {method} failed: {str(e).replace(TOKEN or '-', '***')[:160]}")
        return {}


def say(text: str, buttons=None):
    p = {"chat_id": CHAT_ID, "text": text[:4000], "disable_web_page_preview": True}
    if buttons:
        p["reply_markup"] = {"inline_keyboard": buttons}
    for _ in range(3):
        r = tg("sendMessage", **p)
        if r.get("ok"):
            return r
        time.sleep(2)
    return {}


def download_photo(file_id: str, dest: str) -> bool:
    info = tg("getFile", file_id=file_id)
    path = (info.get("result") or {}).get("file_path")
    if not path:
        return False
    r = requests.get(f"https://api.telegram.org/file/bot{TOKEN}/{path}", timeout=60)
    if r.ok:
        open(dest, "wb").write(r.content)
    return r.ok


# ---------------------------------------------------------------- pending previews
def _load():
    try:
        return json.load(open(PENDING_FILE))
    except Exception:
        return {}


def _save(d):
    json.dump(d, open(PENDING_FILE, "w"))


def add_pending(pv: dict) -> str:
    key = uuid.uuid4().hex[:8]
    with _pending_lock:
        d = {k: v for k, v in _load().items() if time.time() - v.get("t", 0) < 86400}
        d[key] = {**pv, "t": time.time()}
        _save(d)
    return key


def get_pending(key: str):
    with _pending_lock:
        return _load().get(key)


def drop_pending(key: str):
    with _pending_lock:
        d = _load()
        d.pop(key, None)
        _save(d)


# ---------------------------------------------------------------- running jobs
def run_job(args: list, timeout=420) -> dict:
    out = os.path.join(WORK, f"{uuid.uuid4().hex}.json")
    cmd = ["systemd-run", "--scope", "-q", "-p", "MemoryMax=1000M", PY, os.path.join(HERE, "sourcing_job.py"),
           *[str(a) for a in args], out]
    with _job_lock:
        try:
            subprocess.run(cmd, cwd=HERE, timeout=timeout, capture_output=True)
        except subprocess.TimeoutExpired:
            return {"ok": False, "error": "Timed out (Amazon/eBay too slow). Try again."}
    try:
        return json.load(open(out))
    except Exception:
        return {"ok": False, "error": "The job crashed (probably out of memory). Try again in a minute."}
    finally:
        try:
            os.remove(out)
        except OSError:
            pass


def show_result(res: dict):
    if res.get("spec"):
        s = res["spec"]
        head = f"I read: {s.get('product', '?')}" + (f"\nNote: {s['notes']}" if s.get("notes") else "")
    else:
        head = ""
    if not res.get("ok"):
        alts = res.get("alternatives") or []
        btn = [[{"text": f"Use: {a['title'][:40]} (£{a['price']})", "callback_data": f"plan:{a['asin']}"}] for a in alts]
        say(f"{head}\n\nCan't list this: {res.get('error')}", btn or None)
        return
    p = res["preview"]
    key = add_pending(p)
    conf = res.get("confidence")
    lines = [head, "",
             f"MATCH on Amazon UK" + (f" ({conf} confidence: {res.get('reason', '')})" if conf else ""),
             f"  {p['title'][:110]}",
             f"  https://www.amazon.co.uk/dp/{p['asin']}?th=1&psc=1",
             f"  Amazon price: £{p['amazon_shelf']:.2f} (cost incl. VAT £{p['cost_total']:.2f})", "",
             "EBAY PLAN",
             f"  List at: £{p['ebay_price']:.2f}  |  est. profit £{p['est_profit']:.2f}",
             f"  Market avg £{p['market']} | {p['competitors']} competitors",
             f"  Search term: {p['keyword']} | category: {p['category']}"]
    if p["market"] and p["ebay_price"] > p["market"] * 1.10:
        over = (p["ebay_price"] / p["market"] - 1) * 100
        lines.insert(lines.index("EBAY PLAN") + 1,
                     f"  WARNING: our price is {over:.0f}% ABOVE the market average (£{p['market']}) - "
                     "it probably will not sell. Skip unless you have a reason.")
    if p["est_profit"] < 2:
        lines.append(f"  WARNING: estimated profit only £{p['est_profit']:.2f}")
    btns = [[{"text": f"List it at £{p['ebay_price']:.2f}", "callback_data": f"ok:{key}"},
             {"text": "Skip", "callback_data": f"no:{key}"}]]
    for a in (res.get("alternatives") or [])[:3]:
        btns.append([{"text": f"Not it - try: {a['title'][:34]} (£{a['price']})", "callback_data": f"plan:{a['asin']}"}])
    say("\n".join(lines), btns)


# ---------------------------------------------------------------- handlers
def handle_photo(msg):
    photo = max(msg["photo"], key=lambda p: p.get("file_size", 0))
    dest = os.path.join(WORK, f"{uuid.uuid4().hex}.jpg")
    if not download_photo(photo["file_id"], dest):
        say("Couldn't download that image.")
        return
    say("Got it - reading the screenshot and searching Amazon UK (about a minute)...")
    res = run_job(["find", dest, msg.get("caption", "")])
    try:
        os.remove(dest)
    except OSError:
        pass
    show_result(res)


def handle_text(msg):
    text = msg.get("text", "").strip()
    if text.startswith(("/start", "/help")):
        say("Send me a product SCREENSHOT, a LINK from any shop (Amazon, eBay, AliExpress, Argos, Temu, "
            "a brand site... several links are fine), or just describe the product in words. I'll find the exact "
            "item on Amazon UK, price it for eBay and ask you to confirm before listing.\n"
            "Optional note after the link / as a screenshot caption, e.g. 'must be the 2-pack'.")
        return
    url_re = r"https?://[^\s<>\"']+"
    urls = re.findall(url_re, text)
    if urls:
        note = re.sub(url_re, "", text).strip()
        for i, url in enumerate(urls[:6], 1):
            tag = f" ({i}/{len(urls)})" if len(urls) > 1 else ""
            say(f"Reading link{tag}: {url[:70]} ...")
            show_result(run_job(["link", url.rstrip(").,"), note]))
        return
    m = re.search(r"\b(B0[A-Z0-9]{8})\b", text)
    if m:
        say("Checking that Amazon product and the eBay market...")
        show_result(run_job(["plan", m.group(1), text]))
    elif len(text) >= 4:
        say("Searching Amazon UK for that description...")
        show_result(run_job(["text", text]))
    else:
        say("Send a screenshot, a product link or a description of the product.")


def handle_callback(cb):
    tg("answerCallbackQuery", callback_query_id=cb["id"])
    action, _, arg = cb.get("data", "").partition(":")
    if action == "plan":
        say("Checking that product...")
        show_result(run_job(["plan", arg, ""]))
    elif action == "no":
        drop_pending(arg)
        say("Skipped.")
    elif action == "ok":
        p = get_pending(arg)
        if not p:
            say("That preview has expired - send the screenshot again.")
            return
        drop_pending(arg)                      # stops a double tap listing twice
        say(f"Listing at £{p['ebay_price']:.2f}...")
        res = run_job(["list", p["asin"], p["keyword"], p["category"], p["ebay_price"]], timeout=600)
        if res.get("ok"):
            lid = res.get("listing_id")
            say(f"LISTED at £{p['ebay_price']:.2f}: {p['title'][:70]}\n" +
                (f"https://www.ebay.co.uk/itm/{lid}" if lid else "(listing id not found - check Seller Hub)"))
        else:
            say(f"Listing failed: {res.get('error')}")


def dispatch(upd):
    try:
        if "callback_query" in upd:
            cb = upd["callback_query"]
            if str(cb["message"]["chat"]["id"]) == CHAT_ID:
                handle_callback(cb)
            return
        msg = upd.get("message") or {}
        if str((msg.get("chat") or {}).get("id")) != CHAT_ID:
            return                                  # ignore everyone else
        if msg.get("photo"):
            handle_photo(msg)
        elif msg.get("document", {}).get("mime_type", "").startswith("image/"):
            msg["photo"] = [{"file_id": msg["document"]["file_id"], "file_size": 1}]
            handle_photo(msg)
        elif msg.get("text"):
            handle_text(msg)
    except Exception as e:
        log.exception("handler error")
        say(f"Something went wrong: {type(e).__name__}: {str(e)[:150]}")


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s - %(message)s")
    log.info("Telegram listener started")
    offset = None
    while True:
        try:
            r = requests.get(f"{API}/getUpdates", params={"timeout": 30, "offset": offset,
                                                          "allowed_updates": json.dumps(["message", "callback_query"])},
                             timeout=45).json()
            for upd in r.get("result", []):
                offset = upd["update_id"] + 1
                threading.Thread(target=dispatch, args=(upd,), daemon=True).start()
        except Exception as e:
            log.warning(f"poll error: {str(e).replace(TOKEN or '-', '***')[:160]}")
            time.sleep(5)


if __name__ == "__main__":
    main()
