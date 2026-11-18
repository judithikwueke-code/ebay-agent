"""
Turn eBay product pages you opened (captured by the Chrome extension) into listings.

For every captured item that shows real demand (sold today / sold / in baskets / watchers), find the EXACT
same product on Amazon UK (barcode first, then brand+model, then title), check we can undercut the
cheapest quarter of eBay sellers and still earn the minimum profit, and list it.

    python watch_scan.py            # process up to 5 new items (lists for real)
    python watch_scan.py --dry      # show what it would do, list nothing
"""
import json
import logging
import math
import os
import re
import sqlite3
import sys
import time

import amazon_source
from config import VAT_RATE
from db import DB_PATH

log = logging.getLogger("watch_scan")

MIN_SOLD_TODAY = int(os.getenv("WATCH_MIN_SOLD_TODAY", "3"))
MIN_SOLD_TOTAL = int(os.getenv("WATCH_MIN_SOLD", "50"))
MIN_BASKETS = int(os.getenv("WATCH_MIN_BASKETS", "5"))
MIN_WATCHERS = int(os.getenv("WATCH_MIN_WATCHERS", "100"))
MIN_PROFIT = float(os.getenv("MIN_PROFIT_AT_P25", "2"))
# Items pushed by the market radar (source='radar'): eBay listings MEASURED to be selling fast
RADAR_MIN_RATE = float(os.getenv("RADAR_MIN_RATE", "1.0"))           # sales/day needed (half of it from GBP 60 up)
PRICE_FOR_PROFIT = os.getenv("RADAR_PRICE_FOR_PROFIT", "1") == "1"      # owner, 18 Nov: price for OUR profit, never match the eBay seller
RADAR_MAX_OVER_RIVAL = float(os.getenv("RADAR_MAX_OVER_RIVAL", "2.0"))   # sanity only: above this multiple of the fast seller it is probably another product
RADAR_MIN_PROFIT = float(os.getenv("RADAR_MIN_PROFIT", "3"))         # GBP per sale at the fast seller's price
RADAR_UNDERCUT = float(os.getenv("RADAR_UNDERCUT_PCT", "3")) / 100   # go this far under that price when profit allows
RADAR_EQUIV = os.getenv("RADAR_EQUIV", "1") == "1"                   # no exact match: list the closest equivalent
RADAR_EQUIV_MIN_SIM = float(os.getenv("RADAR_EQUIV_MIN_SIM", "0.35"))
# An equivalent is a similar product, not the one measured: never ask more than this multiple of what it costs us
RADAR_EQUIV_MAX_MARKUP = float(os.getenv("RADAR_EQUIV_MAX_MARKUP", "1.5"))
# One of these in the Amazon title but not in the eBay title = an ACCESSORY for the product (a case for the remote)
_ACCESSORY = {"case", "cover", "sleeve", "skin", "holder", "mount", "bracket", "replacement", "compatible", "refill",
              "refills", "spare", "spares", "protector", "strap", "charger", "cable", "adapter", "adaptor", "sticker",
              "stickers", "decal", "decals"}
# ...unless the eBay seller's product is itself that kind of thing under another name (their 'mount' is our 'holder')
_ACCESSORY_KINDS = [{"case", "cover", "sleeve", "skin", "protector"}, {"holder", "mount", "bracket"},
                    {"charger", "cable", "adapter", "adaptor"}, {"sticker", "stickers", "decal", "decals"},
                    {"refill", "refills"}, {"spare", "spares", "replacement"}]
# ...or the word follows one of these: it then names something that comes WITH the product, not what is sold
_INCLUDES = {"with", "includes", "including", "included", "include", "plus", "inc", "incl", "and", "comes", "free",
             "&", "+"}
# 'for' and its relatives: the brand after them is what the product FITS, not who made it ('fit' alone is left out:
# 'Slim Fit' is a cut of clothing)
_FOR = re.compile(r"\b(?:for|compatible with|fits|to fit|works with|replaces)\b")
# products for children need UK safety marking we cannot vouch for. Whole words: 'Kidde' (smoke alarms) is not 'kid'
_CHILD_RE = re.compile(r"\b(?:kids?|kidd(?:y|ie|ies)|child(?:ren)?(?:'?s)?|bab(?:y|ies)|infants?|toddlers?|costumes?|"
                       r"toys?|nursery|newborns?)\b")
# a number at the very start of a title followed by one of these is a feature ('2 Tier', '3 in 1'), not a count
_NOT_A_COUNT = {"tier", "tiers", "door", "doors", "drawer", "drawers", "seater", "way", "ways", "in", "layer", "layers",
                "ply", "speed", "speeds", "mode", "modes", "port", "ports", "person", "man", "year", "years", "day",
                "days", "hour", "hours", "month", "months", "step", "steps", "shelf", "shelves", "slice", "gang", "pin",
                "stage", "section", "sections", "panel", "head", "heads", "wheel", "wheels", "level", "levels",
                "burner", "channel", "channels", "function", "functions", "colour", "colours", "color", "colors",
                "inch", "inches", "ft", "foot", "feet", "metre", "metres", "meter", "meters", "litre", "litres",
                "liter", "liters", "watt", "watts", "volt", "volts", "led", "leds", "ml", "l", "cl", "g", "kg", "mm",
                "cm", "m", "w", "v", "mah", "oz", "lb", "lbs", "gb", "tb", "tog", "amp", "amps", "outlet", "outlets",
                "socket", "sockets", "bottle", "cup", "hole", "arm", "arms", "cube", "cubes", "blade", "light",
                "lights", "bulb", "compartment", "compartments", "pocket", "pockets", "hook", "bar", "bay", "rung",
                "tread", "zone", "ring", "jet", "tray", "basket", "key", "button", "digit", "string", "berth", "room",
                "pot", "core", "cell", "axis", "row", "pole", "leg", "rail", "stroke", "handle", "season", "seasons",
                "degree", "degrees", "star", "point", "player", "players", "sided", "part", "fold", "bit", "mile",
                "litres", "bedroom", "bed", "seat", "seats", "burners", "hob", "slot", "slots", "position",
                "positions", "setting", "settings", "size", "sizes", "pattern", "patterns", "style", "styles",
                "thread", "count", "th", "st", "nd", "rd", "d", "k"}
RADAR_DAILY_LIST_CAP = int(os.getenv("RADAR_DAILY_LIST_CAP", "15"))
_NO_BRAND = {"unbranded", "does not apply", "generic", "handmade", "n/a", "na", "none", "no brand", "not applicable",
             "unknown", "-", "branded", "oem", "no", "see description", "various"}


class AmazonBlocked(Exception):
    """Amazon is showing robot checks - stop the batch and keep the items for later."""


class BrowserFailed(AmazonBlocked):
    """The browser could not read Amazon at all. A failed read is NOT 'no match': keep the item for a later batch."""


class EbayBusy(Exception):
    """eBay's market lookup failed (daily API allowance used up, time-out) - stop the batch, keep the items."""


def _ad_share() -> float:
    """Share of the sale price that goes on the promoted-listing fee (the rate plus VAT on it)."""
    return float(os.getenv("PROMOTE_RATE", "2.0")) / 100 * 1.2 if os.getenv("PROMOTE_CAMPAIGN_ID") else 0.0


def _brand(it: dict) -> str:
    b = (it.get("brand") or "").strip()
    return "" if b.lower() in _NO_BRAND else b


def _is_radar(it: dict) -> bool:
    return (it.get("source") or "") == "radar"


def _words(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", (text or "").lower())


def _names(text: str, phrase: str) -> bool:
    """Does `text` contain every word of `phrase` (a brand name) as a whole word?"""
    need = _words(phrase)
    return bool(need) and set(need) <= set(_words(text))


def brand_named(it: dict) -> str:
    """The fast seller's brand, when their TITLE shows it - buyers search for, and expect, that make."""
    b = _brand(it)
    return b if b and _names(it.get("title") or "", b) else ""


def is_make(title: str, brand: str) -> bool:
    """Does `title` describe a product MADE by `brand`? 'Remote Control for Samsung TV' names Samsung only as the
    thing it fits, so only the words before the first 'for' / 'compatible with' / 'fits' count."""
    low = (title or "").lower()
    m = _FOR.search(low)
    return _names(low[:m.start()] if m else low, brand)


def page_is_make(prod: dict, brand: str) -> bool:
    """Does the Amazon product PAGE say the product is made by `brand`? Where the page states its own brand, that
    decides: a 'Status' blanket is not a Silentnight one however its title is worded."""
    want = set(_words(brand))
    if not want:
        return True
    stated = [b for b in (prod.get("aspects") or {}).get("Brand") or []
              if isinstance(b, str) and b.strip() and b.strip().lower() not in _NO_BRAND]
    if stated:
        return any(have and (want <= have or have <= want) for have in (set(_words(b)) for b in stated))
    return is_make(prod.get("title") or "", brand)


def is_child_product(*titles: str) -> bool:
    """A product for children by its wording (whole words: 'Kidde' alarms and 'Toyota' parts are not)."""
    return any(_CHILD_RE.search((t or "").lower()) for t in titles)


def accessory_for(amazon_title: str, ebay_title: str) -> str:
    """The word showing that the Amazon product is an ACCESSORY for what the eBay seller sells (a case for the remote,
    not the remote); '' when it is the same kind of thing. A word does not count when the seller's own title has it
    or a word for the same kind of thing, nor when it names something that comes with the product ('Dash Cam with
    32GB Card and Mount')."""
    said = set(_words(ebay_title))
    toks = re.findall(r"[a-z0-9]+|[&+]", (amazon_title or "").lower())
    for i, w in enumerate(toks):
        if w not in _ACCESSORY or w in said:
            continue
        if any(w in kind and kind & said for kind in _ACCESSORY_KINDS):
            continue
        if _INCLUDES & set(toks[max(0, i - 4):i]):
            continue
        return w
    return ""


_TRAILING = {"in", "of", "for", "with", "and", "the", "a", "an", "to", "all", "x", "by", "on", "or", "at", "from"}


def shared_keyword(ebay_title: str, amazon_title: str, n: int = 6) -> str:
    """The words the fast seller's title and OUR product's title have in common, in the seller's order: the terms
    buyers search with that are also true of what we will send. The seller's other words can name a different
    brand, size or claim ('Hillington', 'Large', '100% Genuine') and must not reach our title."""
    have = set(_words(amazon_title))
    stems = {w[:5] for w in have if len(w) >= 5}
    out, seen = [], set()
    for w in re.sub(r"[^A-Za-z0-9 ]", " ", ebay_title).split():
        lw = w.lower()
        if lw in seen or not (lw in have or (len(lw) >= 5 and lw[:5] in stems)):
            continue
        seen.add(lw)
        out.append(w.capitalize() if w.isupper() and len(w) > 4 else w)
    if len(out) < 2:                                             # nothing in common to speak of: our product's own words
        out = re.sub(r"[^A-Za-z0-9 ]", " ", amazon_title).split()
    out = out[:n]
    while len(out) > 2 and out[-1].lower() in _TRAILING:         # 'Storage Box With Lid For' -> '... Lid'
        out.pop()
    return " ".join(out)


_STOP = {"the", "and", "for", "with", "new", "set", "pack", "of", "in", "to", "a", "an", "uk", "free", "fast",
         "delivery", "colours", "colour", "color", "assorted", "mixed"}


def _tokens(text: str) -> set[str]:
    return {t for t in re.findall(r"[a-z0-9]+", text.lower()) if len(t) >= 3 and t not in _STOP}


def _numbers(text: str) -> set[str]:
    return set(re.findall(r"\d+(?:\.\d+)?", text.lower()))


def similarity(ebay_title: str, amazon_title: str) -> float:
    a, b = _tokens(ebay_title), _tokens(amazon_title)
    return len(a & b) / len(a | b) if a and b else 0.0


def pack_count(title: str) -> int:
    """How many units the title sells: 'pack of 6', '6 x', '6 pack', '100 pcs'. No wording means 1."""
    t = title.lower()
    for pat in (r"(?:pack|set) of (\d+)", r"pack of (\d+)", r"(\d+)\s?(?:x|pack|pk|pcs|pieces|piece|pairs|pair)\b", r"\bx\s?(\d+)\b"):
        m = re.search(pat, t)
        if m:
            return int(m.group(1))
    return 1


def same_pack(ebay_title: str, amazon_title: str) -> bool:
    """Do both titles sell the same number of units? One of them may say it the short way, with the number at the
    very start: '100 Large Puppy Training Pads' is the same pack as '... Training Pads, 100 Pack'. A leading number
    that counts a feature ('2 Tier Shoe Rack') is not a pack of 2."""
    a, b = pack_count(ebay_title), pack_count(amazon_title)
    if a == b:
        return True
    if 1 not in (a, b):
        return False
    count, quiet = (b, ebay_title) if a == 1 else (a, amazon_title)
    # only a plain number followed by a word ('100 Large ...'; not '100% Cotton', '4.5KW', '18-in-1'), from 10 up
    # (small leading numbers are mostly features: '3 Gun Cabinet', '7 Ton Splitter') and not a year ('2023 New ...')
    m = re.match(r"\s*(\d+)\s+([a-z][a-z']*)", quiet.lower())
    return bool(m) and int(m.group(1)) == count and count >= 10 and not 1900 <= count <= 2100 \
        and m.group(2) not in _NOT_A_COUNT


def same_numbers(ebay_title: str, amazon_title: str) -> bool:
    """Sizes / pack counts in the eBay title (18ml, 2x, 100 pcs) must show up in the Amazon title too,
    and both must sell the same number of units."""
    if not same_pack(ebay_title, amazon_title):
        return False
    need = _numbers(ebay_title)
    if not need:
        return True
    have = _numbers(amazon_title)
    return len(need & have) / len(need) >= 0.6


def coverage(ebay_title: str, amazon_title: str) -> float:
    """Share of the eBay title's words found in the Amazon title (Amazon titles are long, which drags similarity down)."""
    a, b = _tokens(ebay_title), _tokens(amazon_title)
    return len(a & b) / len(a) if a and b else 0.0


def demand_ok(it: dict) -> tuple[bool, str]:
    if _is_radar(it):
        rate, price = it.get("radar_rate") or 0.0, it.get("price") or 0.0
        need = RADAR_MIN_RATE / 2 if price >= 60 else RADAR_MIN_RATE
        kind = "average since listed" if it.get("radar_cls") == "PROV" else "measured"
        if rate >= need:
            return True, f"radar {it.get('radar_cls')}: {rate:g} sold/day {kind}"
        return False, f"radar: {rate:g} sold/day is under {need:g}"
    checks = [("sold today", it.get("sold_today"), MIN_SOLD_TODAY), ("sold", it.get("sold_total"), MIN_SOLD_TOTAL),
              ("in baskets", it.get("in_baskets"), MIN_BASKETS), ("watchers", it.get("watchers"), MIN_WATCHERS)]
    for label, val, need in checks:
        if val is not None and val >= need:
            return True, f"{val} {label}"
    return False, "demand below thresholds"


def _search(q: str, ceiling: float, limit: int = 8) -> list[dict]:
    """Amazon search that says so when the read FAILED (robot check, or the browser broke) - search_amazon itself just
    returns nothing, and 'nothing' used to be recorded as 'no match on Amazon'."""
    for attempt in (1, 2):
        if time.time() < amazon_source._blocked_until:
            raise AmazonBlocked("robot check")
        t0 = time.time()
        cands = amazon_source.search_amazon(q, ceiling, limit=limit, strict=False, min_reviews=5)
        if time.time() < amazon_source._blocked_until:
            raise AmazonBlocked("robot check")
        if cands or amazon_source.last_failure < t0:
            return cands                                         # the page was read (an empty list is a real 'nothing')
        if attempt == 1:
            time.sleep(3)                                        # the browser broke and has been restarted: once more
    raise BrowserFailed("the browser could not read Amazon")


def find_amazon_match(it: dict, pool: list | None = None, max_cost: float | None = None) -> tuple[dict | None, str]:
    """Best Amazon candidate for this eBay item, with the reason it was accepted. Every search result is added to
    `pool`; with `max_cost`, an exact match we can afford beats a closer one we cannot."""
    title = it["title"]
    price = it.get("price") or 20.0
    radar = _is_radar(it)
    ceiling = max(price * 1.5, 15.0) if radar else max(price * 3, 15.0) * (1 + VAT_RATE)
    brand = _brand(it)
    queries = []
    ean = re.sub(r"\D", "", str(it.get("ean") or ""))
    if len(ean) in (8, 12, 13, 14):
        queries.append(("barcode", ean))
    code = next((c for c in (it.get("mpn"), it.get("model"))
                 if c and len(c) >= 4 and any(ch.isdigit() for ch in c) and c.lower() not in _NO_BRAND), None)
    if brand and code:
        queries.append(("brand+model", f"{brand} {code}"))
    words = re.sub(r"[^A-Za-z0-9 ]", " ", title).split()
    queries.append(("title", " ".join(words[:10])))
    if radar:                                                    # second try: just the words that name the product
        core = [w for w in words if len(w) >= 3 and w.lower() not in _STOP and not w.isdigit()][:6]
        if len(core) >= 3 and " ".join(core) != " ".join(words[:10]):
            queries.append(("title", " ".join(core)))

    seen = {c["product_id"] for c in pool} if pool is not None else set()
    for n, (how, q) in enumerate(queries):
        if n == len(queries) - 1 and radar and len(queries) >= 2 and queries[-2][0] == "title" and len(seen) >= 4:
            break                                                # the first title search already gave plenty to judge
        cands = _search(q, ceiling, 10 if radar else 8)
        best, best_key = None, None
        for c in cands:
            if pool is not None and c["product_id"] not in seen:
                seen.add(c["product_id"])
                pool.append(c)
            sim = similarity(title, c["title"])
            if how == "barcode":
                ok = True                                   # Amazon matched the barcode itself
            else:
                ok = sim >= 0.45 and same_numbers(title, c["title"])
                if ok and brand and not is_make(c["title"], brand):
                    ok = False                              # another make, or only something that FITS that make
            key = (max_cost is None or c["cost_gbp"] <= max_cost, sim)
            if ok and (best is None or key > best_key):
                best, best_key = c, key
        if best:
            return best, f"{how} match (similarity {best_key[1]:.2f})"
    return None, "no exact match on Amazon"


def best_equivalent(it: dict, pool: list, max_cost: float) -> tuple[dict | None, str]:
    """No identical product: the closest SIMILAR Amazon product we can still sell at the fast seller's price.
    Same sizes/pack counts, same kind of thing, the same brand when the seller's title names one, real reviews,
    no child products, no protected brands."""
    from deal_scan import BLOCK_WORDS
    from research import _is_policy_blocked, _is_vero
    title = it["title"]
    brand = brand_named(it)
    best, key, dear = None, None, None
    dropped: dict[str, int] = {}                                 # why each candidate was passed over, for the note
    for c in pool:
        low = c["title"].lower()
        sim, cov = similarity(title, c["title"]), coverage(title, c["title"])
        if not (sim >= RADAR_EQUIV_MIN_SIM or (sim >= 0.22 and cov >= 0.55)):
            why = "not similar enough"
        elif not same_numbers(title, c["title"]):
            why = "different size or pack"
        elif is_child_product(title, c["title"]):
            why = "child product"
        elif any(w in low for w in BLOCK_WORDS):
            why = "blocked word"
        elif (c.get("amazon_reviews") or 0) < 20:
            why = "under 20 reviews"
        elif _is_policy_blocked(c["title"]) or _is_vero(c["title"]):
            why = "policy or protected brand"
        elif accessory_for(c["title"], title):                   # an accessory for the product, not the product
            why = "accessory"
        elif brand and not is_make(c["title"], brand):           # what sells fast is the BRAND; another make is not it
            why = "other make"
        elif c["cost_gbp"] < 0.3 * (max_cost + RADAR_MIN_PROFIT):  # far cheaper than the rival: a different product/size
            why = "far too cheap"
        elif c["cost_gbp"] > max_cost:
            dear = c["cost_gbp"] if dear is None else min(dear, c["cost_gbp"])
            why = "too dear"
        else:
            k = (round(sim + cov, 2), -c["cost_gbp"])
            if key is None or k > key:
                best, key = c, k
            continue
        dropped[why] = dropped.get(why, 0) + 1
    if best:
        return best, f"closest equivalent (similarity {similarity(title, best['title']):.2f}, {best.get('amazon_reviews')} reviews)"
    if dear is not None:
        return None, f"DEAR: similar product on Amazon costs GBP {dear:.2f}, most we can pay is GBP {max_cost:.2f}"
    if dropped.get("other make"):
        return None, (f"BRAND: buyers are buying {brand}; Amazon only has other makes "
                      f"({dropped['other make']} similar product(s))")
    seen = (f"{len(pool)} seen: " + ", ".join(f"{n} {why}" for why, n in sorted(dropped.items(), key=lambda x: -x[1]))
            if pool else "the search returned nothing")
    return None, f"no exact match and no similar product on Amazon ({seen})"


_DRY = False


def _ensure_cols():
    conn = sqlite3.connect(DB_PATH)
    for col, typ in (("alternatives", "TEXT"), ("source", "TEXT"), ("radar_rate", "REAL"), ("radar_cls", "TEXT"),
                     ("radar_prio", "REAL"), ("tries", "INTEGER")):
        try:
            conn.execute(f"ALTER TABLE watch_items ADD COLUMN {col} {typ}")
            conn.commit()
        except Exception:
            pass                                                 # already there
    conn.close()


def suggest_alternatives(it: dict, max_alts: int = 3) -> list[dict]:
    """Closest SIMILAR (not identical) Amazon products that could compete with this eBay item."""
    from deal_scan import _ebay_market, _nice, BLOCK_WORDS
    from research import _is_policy_blocked, _is_vero
    title = it["title"]
    q = " ".join(re.sub(r"[^A-Za-z0-9 ]", " ", title).split()[:10])
    cands = amazon_source.search_amazon(q, max(it.get("price") or 20.0, 10.0) * 4 * (1 + VAT_RATE),
                                        limit=10, strict=False, min_reviews=20)
    mk = _ebay_market(title)
    ref = None
    if mk:
        ref = min(mk["p25"], it["price"]) if it.get("price") else mk["p25"]
    elif it.get("price"):
        ref = it["price"]
    if not ref:
        return []
    target = (ref - 0.36) / 1.0413
    alts = []
    for c in cands:
        sim = similarity(title, c["title"])
        low = c["title"].lower()
        if sim < 0.2 or _is_policy_blocked(c["title"]) or _is_vero(c["title"]) or any(w in low for w in BLOCK_WORDS):
            continue
        cost = c["cost_gbp"]
        if target < cost + MIN_PROFIT:
            continue
        price = max(_nice(target, False), _nice(cost + MIN_PROFIT, True))
        child = is_child_product(title, c["title"])
        alts.append({"asin": c["product_id"], "title": c["title"][:90], "amazon_price": round(cost, 2),
                     "list_at": price, "profit": round(price - cost, 2), "similarity": round(sim, 2),
                     "rating": c.get("amazon_rating"), "reviews": c.get("amazon_reviews"), "child_safety": child})
    alts.sort(key=lambda a: (-a["similarity"], -a["profit"]))
    return alts[:max_alts]


def _telegram_alternatives(it: dict, alts: list[dict], why: str):
    try:
        from telegram_bot import _send
    except Exception:
        return
    nl = chr(10)
    lines = ["EBAY ITEM YOU OPENED - NO EXACT AMAZON MATCH", f"  {it['title'][:70]}", f"  eBay: GBP {it.get('price')} | sold today {it.get('sold_today')} | sold {it.get('sold_total')} | watchers {it.get('watchers')}",
             f"  {it.get('url', '')}", f"  ({why})", "", "CLOSEST SIMILAR PRODUCTS (similar, NOT identical):"]
    for i, a in enumerate(alts, 1):
        lines += [f"{i}) {a['title']}", f"   Amazon GBP {a['amazon_price']:.2f} -> list at GBP {a['list_at']:.2f} (profit GBP {a['profit']:.2f}) | similarity {a['similarity']} | {a['rating']} stars, {a['reviews']} reviews",
                  f"   https://www.amazon.co.uk/dp/{a['asin']}?th=1&psc=1"]
        if a["child_safety"]:
            lines.append("   NOTE: child product - check UK safety marking before listing")
    lines += ["", "To list one, tell Claude: list <ASIN> at <price> (it will use Amazon's own photos and title, not the eBay seller's)."]
    _send(nl.join(lines))


def _set(item_id: str, status: str, note: str = "", listing_id: str = ""):
    if _DRY:                      # a dry run reports what it would do and changes nothing
        return
    conn = sqlite3.connect(DB_PATH)
    conn.execute("UPDATE watch_items SET status=?, note=?, listing_id=COALESCE(NULLIF(?,''), listing_id), "
                 "updated_at=? WHERE item_id=?",
                 (status, note[:300], listing_id, time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime()), item_id))
    conn.commit()
    conn.close()


class NoAI(Exception):
    """No AI provider can write listing text (or listing is paused) - stop the batch, keep the items."""


def _strike(item_id: str) -> int:
    """Count a batch in which Amazon could not be read for this item; returns the new count."""
    if _DRY:
        return 0
    conn = sqlite3.connect(DB_PATH)
    try:
        conn.execute("UPDATE watch_items SET tries=COALESCE(tries,0)+1 WHERE item_id=?", (item_id,))
        conn.commit()
        row = conn.execute("SELECT tries FROM watch_items WHERE item_id=?", (item_id,)).fetchone()
        return int(row[0] or 0) if row else 0
    finally:
        conn.close()


def _radar_listed_today() -> int:
    conn = sqlite3.connect(DB_PATH)
    try:
        day = time.strftime("%Y-%m-%d", time.gmtime())
        return conn.execute("SELECT COUNT(*) FROM watch_items WHERE source='radar' AND status='listed' AND updated_at>=?",
                            (day,)).fetchone()[0]
    finally:
        conn.close()


def _process_radar(it: dict, why: str, dry: bool) -> str:
    """List a product an eBay seller is MEASURED to sell fast, at (or just under) that seller's price."""
    from deal_scan import _nice
    ref = it.get("price") or 0.0                                 # buyer-facing total of the fast seller
    if ref < 9:
        _set(it["item_id"], "not_viable", f"price GBP {ref:.2f} too low")
        return "price too low"
    keep = 1 - _ad_share()                                       # what is left of our price after the promoted-listing fee
    max_price = (ref - 0.36) / 1.0413                            # our price if we match them exactly
    under = (ref * (1 - RADAR_UNDERCUT) - 0.36) / 1.0413         # our price if we go a little under them
    max_cost = max_price * keep - RADAR_MIN_PROFIT               # most Amazon can cost (delivered)
    if PRICE_FOR_PROFIT:                                         # price for our profit: allow up to the sanity multiple of their price
        max_cost = max_price * RADAR_MAX_OVER_RIVAL * keep - RADAR_MIN_PROFIT
    pool: list = []
    match, how = find_amazon_match(it, pool, max_cost)
    if match and match["cost_gbp"] + match["shipping_gbp"] > max_cost:
        dear = match["cost_gbp"] + match["shipping_gbp"]
        match = None
        how = f"DEAR: exact match costs GBP {dear:.2f} delivered, most we can pay is GBP {max_cost:.2f}"
        if RADAR_EQUIV:
            eq, how2 = best_equivalent(it, pool, max_cost)
            if eq:
                match, how = eq, how2
    elif not match and RADAR_EQUIV:
        match, how = best_equivalent(it, pool, max_cost)
    if not match:
        _set(it["item_id"], "not_viable" if how.startswith("DEAR") else "no_match", how)
        return how
    cost = match["cost_gbp"] + match["shipping_gbp"]
    equiv = how.startswith("closest equivalent")

    def priced(c: float) -> float:
        """Just under the fast seller, never under our minimum profit - and for a merely similar product never far
        above what it costs (a GBP 20 mini safe was listed at GBP 59.99 because the seller's LARGE one costs 64.90)."""
        floor = _nice((c + RADAR_MIN_PROFIT) / keep, True)
        if PRICE_FOR_PROFIT:
            return floor
        p = max(_nice(under, False), floor)
        return max(min(p, _nice(c * RADAR_EQUIV_MAX_MARKUP, False)), floor) if equiv else p

    price = priced(cost)
    keyword = shared_keyword(it["title"], match["title"])        # only words that are true of OUR product
    if dry:
        return f"WOULD LIST {match['product_id']} Amazon GBP {cost:.2f} -> eBay GBP {price:.2f} ({how}; {why})"
    if _radar_listed_today() >= RADAR_DAILY_LIST_CAP:
        return "DEFER radar daily cap reached"

    from seed_amazon import prepare, SeedSkip
    t0 = time.time()
    try:
        plan = prepare(match["product_id"], keyword, "any", price=price)
    except SeedSkip as e:
        if time.time() < amazon_source._blocked_until:            # the page was not read: that is not a verdict on the item
            raise AmazonBlocked("robot check")
        if amazon_source.last_failure >= t0:
            raise BrowserFailed(f"could not read the Amazon page of {match['product_id']}")
        if "market lookup failed" in str(e):
            raise EbayBusy(str(e))
        _set(it["item_id"], "not_listed", str(e))
        return f"not listed: {e}"
    real = plan["shelf"] + plan["prod"]["shipping_gbp"] * (1 + VAT_RATE)   # real product-page price, delivered
    if (real + RADAR_MIN_PROFIT) / keep > max_price * (RADAR_MAX_OVER_RIVAL if PRICE_FOR_PROFIT else 1.0) + 0.01:
        note = (f"product page GBP {real:.2f} delivered; needs GBP {(real + RADAR_MIN_PROFIT) / keep:.2f} for the profit, "
                f"too far above the fast seller's GBP {max_price:.2f} to be the same kind of product")
        _set(it["item_id"], "not_viable", note)
        return note
    if not same_numbers(it["title"], plan["prod"]["title"]):
        _set(it["item_id"], "no_match", "product page title differs in size/pack count")
        return "pack/size differs on product page"
    price = max(price, priced(real)) if not equiv else priced(real)
    # The fast seller's brand must never be printed on a product of another make
    if _brand(it) and not page_is_make(plan["prod"], _brand(it)):
        if brand_named(it):                                      # the search card looked like that brand; the page is not
            _set(it["item_id"], "no_match", f"BRAND: product page is not {_brand(it)}")
            return f"product page is not {_brand(it)}"
        plan["opp"]["not_brands"] = [_brand(it)]
    import lister
    import llm
    from db import count_listings_today
    lister.DAILY_LISTING_CAP = max(lister.DAILY_LISTING_CAP, count_listings_today() + 1)
    if os.path.exists(lister._PAUSE_FLAG):
        raise NoAI("listing is paused")
    t0 = time.time()
    listing_id = lister.create_listing(plan["opp"], plan["prod"], target_sell_price_gbp=price)
    if listing_id:
        _set(it["item_id"], "listed", f"{how}; {why}; Amazon GBP {real:.2f} -> eBay GBP {price:.2f}", str(listing_id))
        return f"LISTED {match['product_id']} at GBP {price:.2f} (Amazon GBP {real:.2f}; {how}; {why})"
    if llm.last_failure >= t0:                                   # no AI could write the text: the item itself is fine
        raise NoAI("no AI provider could write the listing text")
    _set(it["item_id"], "not_listed", f"{match['product_id']}: listing refused (blocked brand, price floor or policy)")
    return f"not listed ({match['product_id']}: refused)"


def process_item(it: dict, dry: bool = False) -> str:
    ok, why = demand_ok(it)
    if not ok:
        _set(it["item_id"], "low_demand", why)
        return f"low demand ({why})"
    if _is_radar(it):
        return _process_radar(it, why, dry)

    match, how = find_amazon_match(it)
    if not match:
        if os.getenv("WATCH_SUGGEST", "0") == "1":
            try:
                _ensure_cols()
                alts = suggest_alternatives(it)
            except Exception as e:
                log.warning(f"alternatives failed: {e}")
                alts = []
            if alts:
                if not _DRY:
                    conn = sqlite3.connect(DB_PATH)
                    conn.execute("UPDATE watch_items SET alternatives=? WHERE item_id=?", (json.dumps(alts), it["item_id"]))
                    conn.commit()
                    conn.close()
                    _telegram_alternatives(it, alts, how)
                _set(it["item_id"], "suggested", f"{len(alts)} similar product(s) sent to Telegram")
                return f"no exact match; {len(alts)} similar product(s): " + "; ".join(
                    f"{a['asin']} GBP {a['amazon_price']} -> {a['list_at']} (+{a['profit']})" for a in alts)
        _set(it["item_id"], "no_match", how)
        return how

    from deal_scan import _ebay_market, _nice
    mk = _ebay_market(it["title"])
    ref = min(mk["p25"], it["price"]) if mk and it.get("price") else (mk["p25"] if mk else it.get("price"))
    if not ref:
        _set(it["item_id"], "error", "no eBay price reference")
        return "no price reference"
    target = (ref - 0.36) / 1.0413
    cost = match["cost_gbp"]                                    # Amazon shelf price (inc VAT, Prime assumed free delivery)
    if target < cost + MIN_PROFIT:
        note = f"Amazon GBP {cost:.2f} vs eBay price to match GBP {target:.2f}: under GBP {MIN_PROFIT:.0f} profit"
        _set(it["item_id"], "not_viable", note)
        return note
    price = max(_nice(target, False), _nice(cost + MIN_PROFIT, True))
    keyword = shared_keyword(it["title"], match["title"])        # only words that are true of OUR product
    if dry:
        return f"WOULD LIST {match['product_id']} Amazon GBP {cost:.2f} -> eBay GBP {price:.2f} ({how}; {why})"

    from seed_amazon import seed
    listed = seed(match["product_id"], keyword, "any", price=price)
    if listed:
        conn = sqlite3.connect(DB_PATH)
        row = conn.execute("SELECT ebay_listing_id FROM products WHERE supplier='amazon' AND supplier_product_id=?",
                           (match["product_id"],)).fetchone()
        conn.close()
        _set(it["item_id"], "listed", f"{how}; {why}", row[0] if row else "")
        return f"LISTED {match['product_id']} at GBP {price:.2f} ({how}; {why})"
    _set(it["item_id"], "not_listed", f"{match['product_id']}: seed refused (already listed, blocked brand, out of stock or price check)")
    return f"not listed ({match['product_id']}: seed refused)"


def process_watch_items(limit: int = 5, dry: bool = False) -> list[str]:
    global _DRY
    _DRY = dry
    if not dry:
        try:                                     # another job is using the browser: wait (never hold its lock)
            import fcntl
            fh = open("/tmp/growth.lock", "a")
            try:
                fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
                fcntl.flock(fh, fcntl.LOCK_UN)
            except OSError:
                log.info("another browser job (growth run / price check) in progress - watch batch skipped")
                return []
            finally:
                fh.close()
        except ImportError:
            pass
    try:
        _ensure_cols()
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        if not dry:                              # rows a crashed batch left claimed
            conn.execute("UPDATE watch_items SET status='new' WHERE status='processing' AND updated_at < ?",
                         (time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(time.time() - 7200)),))
            conn.commit()
        # today's radar listings are all made: leave radar rows alone (matching them again each batch only spends
        # Amazon page loads) until tomorrow
        skip_radar = not dry and _radar_listed_today() >= RADAR_DAILY_LIST_CAP
        rows = conn.execute(
            "SELECT * FROM watch_items WHERE status='new' " + ("AND COALESCE(source,'')!='radar' " if skip_radar else "")
            + "ORDER BY (COALESCE(source,'')='radar') ASC, "
            "COALESCE(radar_prio,0) DESC, COALESCE(sold_today,0) DESC, COALESCE(watchers,0) DESC LIMIT ?",
            (limit,)).fetchall()
        conn.close()
    except Exception as e:                       # table not created yet (nothing captured)
        log.debug(f"no watch items: {e}")
        return []
    out = []
    try:
        for r in rows:
            it = dict(r)
            if time.time() < amazon_source._blocked_until:
                out.append("Amazon is blocking - batch stopped, items kept")
                break
            if not dry:                          # claim the row so an overlapping batch cannot take it too
                conn = sqlite3.connect(DB_PATH)
                n = conn.execute("UPDATE watch_items SET status='processing', updated_at=? WHERE item_id=? AND status='new'",
                                 (time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime()), it["item_id"])).rowcount
                conn.commit()
                conn.close()
                if not n:
                    continue
            try:
                res = process_item(it, dry=dry)
                if res.startswith("DEFER"):
                    _set(it["item_id"], "new", "")
                    out.append(f"{it['item_id']} {it['title'][:40]}: {res} - batch stopped, items kept")
                    break
            except (AmazonBlocked, NoAI, EbayBusy) as e:
                if isinstance(e, BrowserFailed) and _strike(it["item_id"]) >= 3:
                    _set(it["item_id"], "error", "Amazon could not be read for this item in 3 separate batches")
                else:
                    _set(it["item_id"], "new", "")
                log.warning(f"watch batch stopped at {it['item_id']} ({type(e).__name__}: {e}) - item kept for a later batch")
                out.append(f"batch stopped ({type(e).__name__}: {e}) - items kept")
                break
            except Exception as e:
                _set(it["item_id"], "error", str(e))
                res = f"error: {e}"
            log.info(f"watch item {it['item_id']} '{it['title'][:40]}': {res}")
            out.append(f"{it['item_id']} {it['title'][:40]}: {res}")
    finally:
        amazon_source.close()
    return out


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s - %(message)s")
    for line in process_watch_items(limit=int(os.getenv("WATCH_LIMIT", "5")), dry="--dry" in sys.argv):
        print(line)
