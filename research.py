"""
Finds high-value, fast-selling product opportunities on eBay.co.uk.

Flow:
  1. For each target category, query eBay Browse API for active listings
     (using that category's own price band, not a one-size-fits-all range)
  2. Count active competitors + average current asking price
  3. Score and rank: avg_price / active_listings * category_weight
  4. Filter out VeRO brands and items already listed in our DB
  5. Return top N opportunities

Note on sell-through rate: eBay's legacy Finding API (FindCompletedItems),
which this used to call for historical sold-item data, has been
decommissioned (returns 503 from svcs.ebay.com). Its replacement, the
Marketplace Insights API, requires restricted access this app doesn't have
(403 Insufficient permissions). So this module can only score on *current*
market price and *current* competition — not true historical sales
velocity. If/when Marketplace Insights access is granted, re-add a
sell-through filter on top of this.
"""

import logging
import sqlite3
import requests
from config import (
    EBAY_BROWSE_BASE, get_app_token,
    MIN_PRICE_GBP, MAX_PRICE_GBP, MAX_ACTIVE_COMPETITORS,
    CATEGORIES, VERO_BRANDS,
)
from db import DB_PATH

TERAPEAK_TTL_DAYS = 7  # How long Terapeak data is considered fresh

log = logging.getLogger(__name__)


def _browse_headers() -> dict:
    """Build Browse API headers with a fresh Application token each call."""
    return {
        "Authorization": f"Bearer {get_app_token()}",
        "X-EBAY-C-MARKETPLACE-ID": "EBAY_GB",
        "Content-Type": "application/json",
    }


def _browse_search(keyword: str, category_id: str, limit: int = 50,
                    min_price: float = MIN_PRICE_GBP, max_price: float = MAX_PRICE_GBP) -> list[dict]:
    """Query eBay Browse API for active listings matching keyword + category."""
    url = f"{EBAY_BROWSE_BASE}/item_summary/search"
    params = {
        "q": keyword,
        "category_ids": category_id,
        "filter": f"price:[{min_price}..{max_price}],priceCurrency:GBP,buyingOptions:{{FIXED_PRICE}}",
        "sort": "newlyListed",
        "limit": limit,
    }
    try:
        resp = requests.get(url, headers=_browse_headers(), params=params, timeout=15)
        resp.raise_for_status()
        return resp.json().get("itemSummaries", [])
    except Exception as e:
        log.warning(f"Browse search failed for '{keyword}': {e}")
        return []


def _market_snapshot(keyword: str, category_id: str,
                      min_price: float = MIN_PRICE_GBP, max_price: float = MAX_PRICE_GBP) -> dict:
    """
    Market snapshot using bestMatch sort — what buyers actually SEE and buy from.

    Previous bug: we sorted by 'newlyListed' which returns recently listed items
    (including overpriced unsold stock), giving inflated avg prices that made
    uncompetitive products look viable.

    Now: bestMatch = eBay's promoted listings (most likely to sell). The average
    of the top 10 bestMatch prices IS the competitive price band. If we can't
    profitably compete in that band, we don't list.

    Also enforces MAX_ACTIVE_COMPETITORS at query level via total count.
    """
    url = f"{EBAY_BROWSE_BASE}/item_summary/search"
    params = {
        "q": keyword,
        "category_ids": category_id,
        "filter": f"price:[{min_price}..{max_price}],priceCurrency:GBP,buyingOptions:{{FIXED_PRICE}},conditionIds:{{1000}}",
        "sort": "bestMatch",
        "limit": 10,
    }
    try:
        resp = requests.get(url, headers=_browse_headers(), params=params, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        total = data.get("total", 0)
        items = data.get("itemSummaries", [])
        prices = [float(i["price"]["value"]) for i in items if i.get("price", {}).get("value")]
        # Use median of top-10 bestMatch prices — more robust than mean (outliers skew mean)
        if prices:
            prices.sort()
            mid = len(prices) // 2
            avg_price = round((prices[mid] + prices[~mid]) / 2, 2)
        else:
            avg_price = 0.0
        return {"active_listings": total, "avg_price_gbp": avg_price, "sample": items}
    except Exception as e:
        log.warning(f"Market snapshot failed for '{keyword}': {e}")
        return {"active_listings": 999, "avg_price_gbp": 0.0, "sample": []}


# eBay UK prohibited / restricted item terms.
# Any keyword or product title matching these will be silently dropped.
# Sources: eBay UK Prohibited & Restricted Items policy.
_POLICY_BLOCKED = {
    # Trimmers / strimmers (eBay UK flags these — sharp rotating blades policy)
    "trimmer", "strimmer", "grass trimmer", "hedge trimmer", "beard trimmer",
    "hair trimmer", "weed trimmer", "weed cutter", "line trimmer",
    # Sharp objects & blades
    "cutting disc", "cutting discs", "grinding disc", "grinding discs",
    "abrasive disc", "abrasive discs", "flap disc", "flap discs",
    "saw blade", "saw blades", "jigsaw blade", "jigsaw blades",
    "oscillating blade", "chisel blade", "scraper blade",
    "razor blade", "razor blades", "scalpel", "lancet",
    "knife", "knives", "dagger", "machete", "sword", "swords",
    "throwing star", "shuriken", "knuckle duster", "brass knuckle",
    "flick knife", "flick knives", "gravity knife", "butterfly knife",
    "balisong", "switchblade",
    # Weapons & firearms
    "bb gun", "airsoft gun", "pellet gun", "air pistol",
    "crossbow", "catapult", "slingshot", "baton", "cosh", "truncheon",
    "taser", "stun gun", "pepper spray", "cs spray", "mace spray",
    "nunchuck", "nunchaku",
    # Standalone lithium battery cells (eBay UK bans these unless bundled with a device)
    # Policy: "18650 batteries not allowed unless included with a product designed to use them"
    # Cell type codes that are sold standalone — NOT "includes batteries" or "battery pack"
    "18650", "21700", "26650", "14500", "16340", "cr123a", "cr2032 battery",
    "lithium cell", "loose battery", "bare cell",
    # Hazardous / controlled
    "lockpick", "lock pick", "bump key",
    "explosive", "firework", "pyrotechnic",
    "counterfeit", "replica currency", "fake id",
    # Adult
    "tobacco", "cigarette", "e-cigarette", "vape juice", "vape liquid",
    "drug", "steroid", "anabolic",
}


def _is_policy_blocked(text: str) -> bool:
    text_lower = text.lower()
    return any(term in text_lower for term in _POLICY_BLOCKED)


def _is_vero(title: str) -> bool:
    title_lower = title.lower()
    return any(brand in title_lower for brand in VERO_BRANDS)


def _terapeak_lookup(keyword: str) -> dict | None:
    """
    Returns Terapeak sold-price data for this keyword if collected within
    TERAPEAK_TTL_DAYS days. Returns None if no fresh data exists.
    """
    try:
        conn = sqlite3.connect(DB_PATH)
        row = conn.execute(
            """
            SELECT avg_sold_price, sell_through_pct, total_sold, total_listings, fetched_at
            FROM terapeak_cache
            WHERE keyword = ?
              AND fetched_at >= datetime('now', ?)
            ORDER BY fetched_at DESC LIMIT 1
            """,
            (keyword.strip().lower(), f"-{TERAPEAK_TTL_DAYS} days"),
        ).fetchone()
        conn.close()
        if row and row[0]:
            return {
                "avg_sold_price": row[0],
                "sell_through_pct": row[1],
                "total_sold": row[2],
                "total_listings": row[3],
                "fetched_at": row[4],
            }
    except Exception as e:
        log.debug(f"Terapeak lookup failed: {e}")
    return None


def _score(avg_price: float, active: int, weight: float) -> float:
    """
    Higher avg market price + lower competition + higher category weight
    = better opportunity. Uses sqrt(active) so heavily-competed categories
    (e.g. furniture with 5000 listings) rank lower but still appear, rather
    than being rejected entirely — a £300 ottoman with 300 competitors is
    still a better opportunity than a £20 gadget with 2 competitors.
    """
    if active == 0:
        return 0
    import math
    return (avg_price / math.sqrt(active)) * weight


def find_opportunities(top_n: int = 20) -> list[dict]:
    """
    Main entry point. Returns top_n ranked product opportunities.
    Each item: {keyword, category, category_id, avg_price_gbp,
                active_listings, score, sample_title, sample_image_url}
    """
    # Seed keywords per category.
    # Furniture: only ottoman keywords — other furniture (sofa, wardrobe, dining
    # table, TV unit, bookcase, chest of drawers) removed until Birlea/Wholesale
    # Domestic trade accounts are live and those suppliers return results.
    # All other categories use CJ-UK-warehouse-friendly keywords (compact, non-bulky
    # items CJ stocks in their GB warehouse). Mattresses disabled — unviable at
    # Wholesale Beds QTY 1-7 pricing.
    seed_keywords = {
        "mattress": [],
        "furniture": [
            "ottoman storage bed double", "ottoman storage bed king",
            "ottoman bed grey fabric", "ottoman bed super king",
            "ottoman divan bed king", "velvet ottoman bed",
            "ottoman bed white", "ottoman bed black fabric",
        ],
        "home_appliances": [
            # Small kitchen appliances
            "led strip lights smart", "portable fan desk", "air fryer compact",
            "mini projector portable", "electric panel heater", "dehumidifier",
            "steam cleaner handheld", "portable air conditioner", "electric kettle stainless steel",
            "robot vacuum cleaner", "air purifier hepa", "tower fan oscillating",
            "coffee maker drip", "food processor compact", "electric wine opener",
            "vacuum sealer food", "electric egg cooker", "milk frother handheld",
            "sous vide cooker", "electric grill indoor", "bread maker machine",
            "ice cream maker", "juicer cold press", "electric pizza oven",
            "sandwich maker", "waffle maker", "electric crepe maker",
            "popcorn maker electric", "slow cooker mini", "rice cooker 1l",
            "coffee grinder electric", "electric can opener", "hand mixer electric",
            "stand mixer compact", "electric spiralizer", "salad spinner electric",
            # Home comfort
            "heated blanket electric", "heated throw", "electric foot warmer",
            "desk fan with light", "table lamp touch dimmer", "led floor lamp",
            "smart plug wifi", "extension lead usb", "wireless charger pad",
        ],
        "tools_hardware": [
            "laser measure digital", "electric screwdriver cordless",
            "stud finder wall", "cable tester kit",
            "cordless drill set", "laser level self levelling",
            "impact driver cordless", "oscillating multi tool",
            "heat gun electric", "rivet gun riveter",
            "wire stripper automatic", "soldering iron kit",
            "work light led rechargeable", "magnetic wristband tools",
            "electric planer wood", "rotary tool kit",
            "multimeter digital", "clamp meter digital",
            "electric sander detail", "random orbital sander",
            "pipe cutter tube", "spirit level aluminium",
            "tool bag organiser", "screwdriver set precision",
        ],
        "auto_parts": [
            "wireless phone holder car", "tyre inflator portable electric",
            "jump starter compact", "car vacuum cordless",
            "cordless car vacuum handheld", "dashcam dual lens 1080p", "obd2 diagnostic scanner",
            "car seat cushion lumbar", "steering wheel cover leather",
            "car air freshener vent", "car organiser back seat",
            "windscreen ice scraper", "car tyre pressure gauge",
            "led interior car lights", "car phone charger wireless",
            "parking sensor kit", "car boot organiser",
            "car seat gap filler", "car headrest hook", "car cup holder expander",
            "windscreen sun shade", "car rubbish bin", "car snow brush",
            "led number plate light", "car door handle cover",
            "tow bar bike rack", "reversing camera wireless",
        ],
        "sports_fitness": [
            "resistance bands set", "yoga mat thick non slip",
            "ab wheel roller", "jump rope speed",
            "pull up bar doorway", "dumbbells set adjustable",
            "foam roller muscle", "balance board fitness",
            "battle rope exercise", "kettlebell adjustable",
            "push up bars handles", "ankle weights pair",
            "gym gloves weight lifting", "exercise bike stationary",
            "chin up bar ceiling", "agility ladder training",
            # New sports
            "yoga blocks set", "gym bag holdall", "sports water bottle",
            "cycling gloves", "running belt phone", "swim goggles anti fog",
            "fitness tracker band", "jump rope weighted handle",
            "muscle roller stick", "knee sleeve support",
            "wrist wraps gym", "weight lifting belt",
            "skipping rope digital counter", "punch bag gloves",
            "resistance loop bands mini", "hip circle band",
            "suspension trainer straps", "dip belt weight",
        ],
        "health_devices": [
            "percussion massage gun portable", "eye massager heated",
            "foot massager electric", "tens machine pain relief",
            "massage gun deep tissue", "red light therapy device",
            "neck massager cordless", "back massager cushion",
            "infrared thermometer forehead", "blood pressure monitor wrist",
            "pulse oximeter fingertip", "electric toothbrush sonic",
            "water flosser cordless", "hair removal laser ipl",
            "led face mask therapy", "gua sha tool set",
            "scalp massager electric", "heated eye mask",
            # Beauty & self care
            "facial steamer portable", "jade roller face",
            "nail drill electric set", "eyelash curler heated",
            "makeup brush set professional", "dermaplaning tool",
            "blackhead remover vacuum", "ice roller face",
            "hair straightener brush", "curling wand ceramic",
            "hair dryer diffuser", "electric back scrubber",
            "callus remover electric foot", "nail lamp uv led",
        ],
        "garden_outdoor": [
            "solar garden lights outdoor", "outdoor security camera wireless",
            "garden hose expandable", "led solar string lights",
            "rattan garden furniture set", "electric lawn mower cordless",
            "garden sprinkler automatic", "kneeler garden seat",
            "hedge trimmer cordless", "leaf blower cordless",
            "pressure washer electric", "garden tool set",
            "raised garden bed planter", "solar powered fountain pump",
            "bird feeder hanging", "outdoor thermometer wireless",
            "bbq grill portable", "camping lantern rechargeable",
            "outdoor solar spotlight", "patio heater electric",
            # New outdoor/garden
            "camping chair folding", "camping table portable",
            "garden parasol umbrella", "gazebo pop up", "garden bench cushion",
            "plant pot large indoor", "window box planter",
            "grow light led indoor", "plant mister spray bottle",
            "garden hose reel wall mounted", "outdoor solar fairy lights",
            "log store firewood", "garden storage box",
            "hammock garden outdoor", "outdoor rug waterproof",
            "camping sleeping bag", "tent footprint groundsheet",
            "hiking backpack daypack", "waterproof jacket packaway",
        ],
        "home_living": [
            # Home decor
            "led bathroom mirror", "full length mirror freestanding",
            "wall clock large modern", "picture frame set multipack",
            "cushion cover set velvet", "throw blanket sofa",
            "blackout curtain eyelet", "roman blind easy fit",
            "scented candle set luxury", "reed diffuser set",
            "artificial plant realistic", "dried flower bouquet",
            "photo frame collage wall", "canvas wall art",
            # Storage & organisation
            "under bed storage bag", "wardrobe organiser hanging",
            "drawer divider organiser", "shoe rack stackable",
            "over door organiser", "cable management box",
            "makeup organiser acrylic", "kitchen drawer organiser",
            "fridge organisation set", "pantry storage container",
            "laundry basket wicker", "ironing board cover",
            # Cleaning
            "steam mop floor", "window vacuum cleaner",
            "lint roller refill", "rubber broom carpet",
            "dishwasher cleaning tablet", "washing machine cleaner",
        ],
        "tech_accessories": [
            # Phone & laptop
            "wireless earbuds bluetooth", "phone stand adjustable",
            "laptop stand portable", "usb hub multiport",
            "ring light tripod selfie", "webcam hd 1080p",
            "mechanical keyboard compact", "mouse wireless silent",
            "monitor riser stand", "laptop cooling pad",
            "phone wallet case", "screen protector pack",
            "power bank 20000mah", "magnetic phone holder",
            "cable organiser clips", "desk charging station",
            "smart watch fitness android", "tablet stand holder",
            "keyboard wrist rest", "gaming mouse pad xl",
        ],
        "pet_supplies": [
            "cat scratching post large", "dog puzzle toy interactive",
            "pet water fountain filter", "cat tunnel collapsible",
            "dog grooming brush deshedding", "pet hair remover roller",
            "cat toy interactive feather", "dog training collar",
            "pet blanket waterproof", "dog bed orthopaedic",
            "cat tree activity centre", "bird cage accessories",
            "fish tank filter", "hamster cage large",
            "dog lead retractable", "pet carrier bag",
            "cat litter mat", "dog poop bag dispenser",
            "pet feeding mat", "automatic pet feeder",
        ],
        "baby_kids": [
            "baby monitor video wifi", "play mat foam tiles",
            "baby bouncer rocker", "nursing pillow breastfeeding",
            "baby bath seat support", "baby food maker blender",
            "kids headphones volume limited", "bath toy set",
            "children art easel", "kids telescope beginner",
            "lego compatible building blocks", "remote control car kids",
            "walkie talkie kids set", "kids karaoke microphone",
            "magnetic drawing board", "kinetic sand set",
            "bubble machine outdoor", "sprinkler pad kids",
            "kids camping tent indoor", "glow in dark stars",
        ],
        "office_stationery": [
            "document shredder home", "laminator a4",
            "label maker machine", "desk organiser bamboo",
            "monitor stand riser drawer", "cable management raceway",
            "stapler heavy duty", "hole punch electric",
            "whiteboard magnetic home", "cork board notice board",
            "planner notebook hardcover", "pen set calligraphy",
            "sticky note dispenser", "tape dispenser weighted",
            "paper shredder cross cut", "binding machine comb",
        ],
    }

    # Pull fresh trending keywords every cycle and merge with seeds
    try:
        from trend_research import get_fresh_keywords
        fresh = get_fresh_keywords(max_total=40)
        for item in fresh:
            cat = item.get("category", "home_appliances")
            if cat in seed_keywords:
                seed_keywords[cat].append(item["keyword"])
            else:
                seed_keywords.setdefault(cat, []).append(item["keyword"])
        log.info(f"Injected {len(fresh)} fresh trending keywords from daily discovery")
    except Exception as e:
        log.warning(f"Trend research skipped: {e}")

    results = []

    for cat_key, cat_info in CATEGORIES.items():
        cat_id = cat_info["id"]
        weight = cat_info["weight"]
        min_price = cat_info.get("min_price", MIN_PRICE_GBP)
        max_price = cat_info.get("max_price", MAX_PRICE_GBP)
        keywords = seed_keywords.get(cat_key, [])
        seen_kws = set()

        for kw in keywords:
            if kw in seen_kws:
                continue
            seen_kws.add(kw)
            snap = _market_snapshot(kw, cat_id, min_price, max_price)
            active = snap["active_listings"]
            avg_price = snap["avg_price_gbp"]

            # Hard cap: >300 sellers = race-to-bottom commodity, skip
            if active > 300:
                log.debug(f"Skip '{kw}': {active} active competitors (too saturated)")
                continue
            if avg_price == 0:
                log.debug(f"Skip '{kw}': no active listings found in price band")
                continue

            # Terapeak override: if we have real sold-price data from the extension,
            # use it instead of the bestMatch active-listing proxy.
            terapeak = _terapeak_lookup(kw)
            if terapeak and terapeak["avg_sold_price"]:
                log.info(
                    f"Terapeak data for '{kw}': avg_sold=£{terapeak['avg_sold_price']} "
                    f"({terapeak['sell_through_pct']}% sell-through, {terapeak['total_sold']} sold) "
                    f"— overriding bestMatch estimate £{avg_price}"
                )
                avg_price = terapeak["avg_sold_price"]
                # Low sell-through = slow-moving product, lower score
                if terapeak["sell_through_pct"] and terapeak["sell_through_pct"] < 20:
                    log.debug(f"  Low sell-through {terapeak['sell_through_pct']}% — deprioritising")
                    avg_price *= 0.5  # halve effective price to reduce score

            samples = snap["sample"]
            sample_title = samples[0].get("title", kw) if samples else kw
            sample_image = (
                samples[0].get("image", {}).get("imageUrl", "") if samples else ""
            )
            # Real titles currently ranking — fed to Claude in lister.py to mine
            # actual keyword variants/word order instead of guessing blind.
            competitor_titles = [s.get("title", "") for s in samples[:8] if s.get("title")]

            if _is_policy_blocked(kw):
                log.warning(f"Skip '{kw}': matches eBay policy blocked term")
                continue

            if _is_vero(sample_title):
                log.debug(f"Skip '{kw}': VeRO brand detected in '{sample_title}'")
                continue

            if _is_policy_blocked(sample_title):
                log.warning(f"Skip '{kw}': top result '{sample_title[:60]}' matches policy blocked term")
                continue

            score = _score(avg_price, max(active, 1), weight)

            results.append({
                "keyword": kw,
                "category": cat_key,
                "category_id": cat_id,
                "avg_price_gbp": avg_price,
                "active_listings": active,
                "score": round(score, 4),
                "sample_title": sample_title,
                "sample_image_url": sample_image,
                "competitor_titles": competitor_titles,
            })
            log.info(f"Opportunity: '{kw}' | price=£{avg_price} | competitors={active} | score={score:.3f}")

    results.sort(key=lambda x: x["score"], reverse=True)
    return results[:top_n]


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    opps = find_opportunities(top_n=10)
    print(f"\nTop {len(opps)} opportunities:\n")
    for i, o in enumerate(opps, 1):
        print(f"{i:2}. [{o['category']}] {o['keyword']}")
        print(f"    £{o['avg_price_gbp']} | {o['active_listings']} competitors | score {o['score']}")
