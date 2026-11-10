"""
Shared pieces of the market radar (see market_radar.py): settings, the radar database, the category "cells" it sweeps,
title filters and the sales-velocity maths.
"""
import json
import os
import re
import sqlite3
from datetime import datetime
from zoneinfo import ZoneInfo

HERE = os.path.dirname(os.path.abspath(__file__))
RADAR_DB = os.path.join(HERE, "radar.db")
TREE_FILE = os.path.join(HERE, ".ebay_category_tree_gb.json")
UK = ZoneInfo("Europe/London")
OWN_SELLER = os.getenv("EBAY_OWN_SELLER", "yagz-emporium").lower()

BANDS = {"A": (8, 60), "B": (60, 500)}          # item price bands in GBP: everyday / high-ticket
NEW_DAYS = 21                                    # "new listing" sweep looks at listings started in the last 21 days...
NEW_MIN_DAYS = 3                                 # ...but not the last 3: eBay boosts just-listed items whatever they sell
BEST_EVERY_H, NEW_EVERY_H, WATCH_EVERY_H = 70, 22, 46
STORE_TOP = {"best": 60, "new": 45, "watch": 25, "seller": 20}
MIN_GAP_H, WINDOW_H = 5, 72                      # a sales rate needs two snapshots at least 5 h apart, inside 72 h
FAST_CLASSES = ("ROCKET", "FAST", "RISING", "STEADY", "HIGH")
CLS_W = {"ROCKET": 6.0, "FAST": 5.0, "RISING": 4.0, "HIGH": 3.5, "STEADY": 3.0, "PROV": 1.5}

# Top-level eBay UK categories the radar covers: id -> (label, weight)
ROOTS = {
    "11700": ("Home, Furniture & DIY", 1.0), "159912": ("Garden & Patio", 0.9), "1281": ("Pet Supplies", 0.95),
    "26395": ("Health & Beauty", 0.8), "220": ("Toys & Games", 0.85), "888": ("Sporting Goods", 0.65),
    "131090": ("Vehicle Parts & Accessories", 0.6), "58058": ("Computers/Tablets & Networking", 0.6),
    "15032": ("Mobile Phones & Communication", 0.6), "293": ("Sound & Vision", 0.55), "14339": ("Crafts", 0.6),
    "12576": ("Business, Office & Industrial", 0.55),
}
SKIP_NAMES = (
    "vintage", "manuals", "food & drink", "vitamins", "smoking", "natural & alternative", "vision care", "tattoos",
    "businesses for sale", "web domains", "services", "literature", "wholesale", "sim cards", "software",
    "mobile & smart phones", "dummy mobile", "pda", "laptops & netbooks", "desktops", "tablets & ebook readers",
    "hunting", "archery", "paintball", "military", "aircraft", "sidecars", "plants, seeds", "beekeeping", "poultry",
    "reptiles", "spiders", "fragrances", "shaving", "football shirts", "sport awards", "pet memorials", "wedding",
    "metaphysical", "beanies", "toy soldiers", "wargames", "diecast", "scalextric", "fast food", "healthcare, lab",
    "cnc", "industrial automation", "hvac", "modular", "printing & graphic", "agriculture", "video broadcasting",
    "telephone & answering", "enterprise networking", "disabled vehicle", "golf cart", "commercial vehicle", "atv",
    "trials & motocross", "radio communication", "home phones", "phone parts", "vehicle services", "smart glasses",
    "virtual reality", "dvd, blu-ray", "tvs", "medical & mobility", "health care", "oral care", "sun care",
)
SKIP_IDS = {"14737", "767", "49019", "2631", "16486", "222", "2616", "717", "1188"}
CORE = {
    "299", "631", "20625", "20444", "43502", "20697", "10033", "26677", "30565", "20710", "41968", "16086", "16092",
    "177073", "20742", "20737", "20754", "11838", "11854", "11863", "36447", "47945", "15273", "16034", "7294", "9394",
    "31530", "82099", "25298", "46576", "109471", "29518", "181033", "149242", "20498", "42154", "29521", "181034",
    "233", "11731", "19169", "11743", "28102", "11783", "160737", "34998", "14961", "15052", "182094", "3676", "177017",
}
SPORT_ALLOW = {"15273", "16034", "7294", "1492", "1513", "74050", "30100", "72575", "11330", "20862", "3153", "36259",
               "13340", "21567", "16262", "36279", "30105", "16258"}
# Second-level categories that also get a high-ticket (band B) sweep - saunas/hot tubs, furniture, power tools, fitness...
B_CATS = {"20727", "25863", "29518", "43560", "149242", "177017", "3197", "20710", "30565", "631", "20444", "20697",
          "15273", "7294", "16034", "36447", "11743", "34998", "61573", "26221", "20742", "20737", "177073", "38219"}

# Listings the agent could never source from Amazon or should not copy
SKIP_WORDS = (
    "refurbished", "pre-owned", "preowned", "spares or repair", "job lot", "joblot", "wholesale", "personalised",
    "personalized", "custom made", "made to measure", "cut to size", "gift card", "voucher", "ebook", "digital download",
    "licence key", "license key", "subscription", "ex-display", "ex display", "mystery box", "kiln dried", "firewood",
    "smokeless", "house coal", "grass seed", "topsoil", "top soil", "compost", "bark chip", "gravel", "chippings",
    "cement", "paving", "turf", "live plant", "per metre", "per meter", "by the metre", "choose your", "any name",
    "baby", "nappy", "nappies", "vape", "clearance", "faulty", "used ", "bulbs in the green", "bare root", "logs ",
)
_PLACEHOLDER_BRANDS = {"unbranded", "does not apply", "generic", "handmade", "n/a", "na", "none", "no brand",
                       "not applicable", "unknown", "-", "branded", "oem", "no", "see description", "various"}
_COMPAT_RE = re.compile(r"\b(?:for|fits?|compatible with|suitable for)\s+(?:the\s+)?"
                        r"(?:audi|bmw|mercedes|volkswagen|ford|vauxhall|honda)\b")
_STOP = {"the", "and", "for", "with", "new", "set", "pack", "of", "in", "to", "a", "an", "uk", "free", "fast", "delivery",
         "colours", "colour", "color", "assorted", "mixed", "large", "small", "medium", "black", "white", "grey", "gray",
         "blue", "red", "green", "pink", "size", "sizes", "pcs", "piece", "pieces", "from", "your", "all", "quality",
         "high", "heavy", "duty", "premium", "home", "x", "or", "on", "by", "post", "stock", "seller", "brand", "2026",
         "2025", "extra", "mini", "best", "sale", "cheap", "next", "day", "1st", "class", "royal", "mail"}
_pol = None
_vero = None


def clean_brand(b):
    b = (b or "").strip()
    return None if not b or b.lower() in _PLACEHOLDER_BRANDS else b


def title_blocked(title: str):
    """Reason this listing is ignored (restricted goods, protected brand, unsourceable), or None."""
    global _pol, _vero
    if _pol is None:
        from config import VERO_BRANDS
        from research import _is_policy_blocked
        _pol = _is_policy_blocked
        _vero = re.compile(r"\b(?:" + "|".join(re.escape(b.lower()) for b in VERO_BRANDS if b) + r")\b")
    low = (title or "").lower() + " "
    if _pol(low):
        return "policy"
    for w in SKIP_WORDS:
        if w in low:
            return "skip"
    if _vero.search(_COMPAT_RE.sub(" ", low)):
        return "vero"
    return None


def tokens(title: str) -> list:
    return [t for t in re.findall(r"[a-z]+[a-z0-9]*|[0-9]+[a-z]+", (title or "").lower()) if len(t) >= 3 and t not in _STOP]


def bigrams(title: str) -> set:
    t = tokens(title)
    return {t[i] + " " + t[i + 1] for i in range(len(t) - 1)}


def jaccard(a: str, b: str) -> float:
    x, y = set(tokens(a)), set(tokens(b))
    return len(x & y) / len(x | y) if x and y else 0.0


# ---------------------------------------------------------------- velocity maths
# Share of a UK day's online sales that falls in each hour (00:00 .. 23:00, Europe/London). A snapshot window that
# covers 01:00-07:00 is NOT a quarter of a selling day, so windows are measured in "selling days" with these weights.
_HOUR_W = [1.0, 0.6, 0.4, 0.4, 0.5, 0.9, 2.0, 3.5, 4.5, 5.2, 5.6, 5.6, 5.6, 5.4, 5.3, 5.3, 5.5, 5.9, 6.4, 7.0, 7.4, 6.8, 4.6, 2.6]
_HW_SUM = sum(_HOUR_W)


def eff_days(t0: float, t1: float) -> float:
    """Selling days between two epoch times (whole days count 1.0; the remainder is weighted by UK hour of day)."""
    if t1 <= t0:
        return 0.0
    whole = int((t1 - t0) // 86400)
    t, total = t0 + whole * 86400, 0.0
    while t < t1 - 1e-6:
        dt = datetime.fromtimestamp(t, UK)
        end = min(t + 3600 - (dt.minute * 60 + dt.second + dt.microsecond / 1e6), t1)
        total += (end - t) / 3600 * _HOUR_W[dt.hour]
        t = end
    return whole + total / _HW_SUM


def age_days(created, now: float):
    if not created:
        return None
    try:
        return max((now - datetime.fromisoformat(created.replace("Z", "+00:00")).timestamp()) / 86400, 0.0)
    except Exception:
        return None


def classify(age, d_sold, rate, life_rate, total_price):
    """Class from a MEASURED rate (sales between two snapshots / selling days)."""
    if rate is None:
        return None
    if d_sold >= 3 and rate >= 2:
        return "ROCKET" if age is not None and age <= 30 else "FAST"
    if d_sold >= 2 and rate >= 1:
        if age is not None and age >= 14 and rate >= 1.8 * max(life_rate or 0, 0.05):
            return "RISING"
        return "STEADY"
    if total_price >= 60 and d_sold >= 1 and rate >= 0.5:
        return "HIGH"
    return "SLOW"


def provisional(age, sold, life_rate, total_price, top: bool):
    """With ONE snapshot there is only a lifetime average; flag the ones worth acting on before confirmation."""
    if age is not None and 2 <= age <= 30 and sold >= 15 and life_rate >= 1.5:
        return "PROV"
    if sold >= 60 and life_rate >= 2 and top:
        return "PROV"
    if total_price >= 60 and sold >= 20 and life_rate >= 0.5 and age is not None and age <= 120:
        return "PROV"
    return None


# ---------------------------------------------------------------- database
_SCHEMA = """
CREATE TABLE IF NOT EXISTS items (
  key TEXT PRIMARY KEY, item_id TEXT, is_group INTEGER DEFAULT 0, title TEXT, price REAL, ship REAL, seller TEXT,
  seller_fb INTEGER, root TEXT, cell TEXT, cat_id TEXT, image TEXT, url TEXT, brand TEXT, mpn TEXT, gtin TEXT, epid TEXT,
  created TEXT, sources TEXT DEFAULT '', best_rank INTEGER, new_rank INTEGER, watch_rank INTEGER, seller_rank INTEGER,
  watch_count INTEGER, promoted INTEGER DEFAULT 0, prio REAL DEFAULT 0, first_seen REAL, last_seen REAL,
  sold INTEGER, avail INTEGER, snaps INTEGER DEFAULT 0, first_snap REAL, last_snap REAL, next_snap REAL,
  life_rate REAL, rate REAL, rate_prev REAL, d_sold INTEGER, win_days REAL, accel REAL, cls TEXT,
  status TEXT DEFAULT 'queued', pushed_at REAL, push_rate REAL, outcome TEXT, outcome_note TEXT, listing_id TEXT, note TEXT);
CREATE INDEX IF NOT EXISTS ix_items_status ON items(status, next_snap);
CREATE INDEX IF NOT EXISTS ix_items_prio ON items(status, prio);
CREATE INDEX IF NOT EXISTS ix_items_cell ON items(cell);
CREATE TABLE IF NOT EXISTS snaps (key TEXT, ts REAL, sold INTEGER, avail INTEGER, price REAL);
CREATE INDEX IF NOT EXISTS ix_snaps ON snaps(key, ts);
CREATE TABLE IF NOT EXISTS cells (
  cat_id TEXT, band TEXT, name TEXT, root TEXT, depth INTEGER, weight REAL, parent TEXT, last_best REAL, last_new REAL,
  last_watched REAL, seen INTEGER DEFAULT 0, snapped INTEGER DEFAULT 0, fast INTEGER DEFAULT 0, viable INTEGER DEFAULT 0,
  drilled INTEGER DEFAULT 0, PRIMARY KEY (cat_id, band));
CREATE TABLE IF NOT EXISTS sellers (seller TEXT PRIMARY KEY, fb INTEGER, fast INTEGER DEFAULT 0, viable INTEGER DEFAULT 0,
  items INTEGER DEFAULT 0, last_sweep REAL, root TEXT);
CREATE TABLE IF NOT EXISTS terms (day TEXT, root TEXT, term TEXT, n_new INTEGER DEFAULT 0, n_best INTEGER DEFAULT 0,
  PRIMARY KEY (day, root, term));
CREATE TABLE IF NOT EXISTS runs (id INTEGER PRIMARY KEY AUTOINCREMENT, started REAL, ended REAL, calls INTEGER,
  sweeps INTEGER, first_snaps INTEGER, resnaps INTEGER, pushed INTEGER, note TEXT);
"""


def connect() -> sqlite3.Connection:
    c = sqlite3.connect(RADAR_DB, timeout=60)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL")
    c.executescript(_SCHEMA)
    return c


# ---------------------------------------------------------------- category cells
_tree_index = None


def tree_index() -> dict:
    """category id -> tree node (needs the saved taxonomy tree file)."""
    global _tree_index
    if _tree_index is None:
        _tree_index = {}
        stack = [json.load(open(TREE_FILE, encoding="utf-8"))["rootCategoryNode"]]
        while stack:
            n = stack.pop()
            _tree_index[n["category"]["categoryId"]] = n
            stack.extend(n.get("childCategoryTreeNodes", []))
    return _tree_index


def _skip(cat_id: str, name: str) -> bool:
    low = name.lower()
    return cat_id in SKIP_IDS or low.startswith("other ") or any(s in low for s in SKIP_NAMES)


def build_cells() -> list:
    """(cat_id, band, name, root, depth, weight, parent) for every cell the radar sweeps."""
    idx, out = tree_index(), []
    for rid, (label, w) in ROOTS.items():
        node = idx.get(rid)
        if not node:
            continue
        out.append((rid, "B", label + " (all, high-ticket)", rid, 1, round(w * 0.9, 3), ""))
        for ch in node.get("childCategoryTreeNodes", []):
            cid, name = ch["category"]["categoryId"], ch["category"]["categoryName"]
            if _skip(cid, name) or (rid == "888" and cid not in SPORT_ALLOW):
                continue
            wa = round(w * (1.2 if cid in CORE else 1.0), 3)
            out.append((cid, "A", name, rid, 2, wa, rid))
            if cid in B_CATS:
                out.append((cid, "B", name + " (high-ticket)", rid, 2, round(w, 3), rid))
            for g in ch.get("childCategoryTreeNodes", []):       # band W = most-watched list only (free API)
                gid, gname = g["category"]["categoryId"], g["category"]["categoryName"]
                if not _skip(gid, gname):
                    out.append((gid, "W", name + " > " + gname, rid, 3, wa, cid))
    return out


def child_cells(cat_id: str, band: str, root: str, weight: float, depth: int) -> list:
    node = tree_index().get(cat_id) or {}
    out = []
    for ch in node.get("childCategoryTreeNodes", []):
        cid, name = ch["category"]["categoryId"], ch["category"]["categoryName"]
        if not _skip(cid, name):
            out.append((cid, band, name, root, depth + 1, round(weight * 1.05, 3), cat_id))
    return out
