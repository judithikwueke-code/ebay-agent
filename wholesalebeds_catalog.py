"""
Local catalog for Wholesale Beds (wholesalebeds.co.uk) — Yagz Ltd trade account.

Wholesale Beds has no public REST API; ordering is by phone/email
(DHD@WHOLESALEBEDS.CO.UK) against their published price list. This module
mirrors that price list as static data, extracted from "Issue 15 Price List"
(effective 01/01/2026), qty 1-7 trade pricing, so the agent can compute real
margins and generate listings without a live API call.

Delivery: Zone 1 DHD 2MAN rate from page 28 (£45 for ottomans and mattresses).
Zone 1 covers most of England south of Sheffield — the majority of UK eBay
buyer locations. Orders to London (£48), Zone 2 (£50), Zone 3 (£55), or Zone
4 (£65) will have slightly lower actual margins than the catalog estimates.

Image ordering: lifestyle/room-set first (best for furniture conversion on
eBay), then white-background shot if available, then close-up detail shots.
King/super king variants share the full image set from their double sibling —
same bed design, different size, all photos identical.

Re-scrape and update this file whenever Wholesale Beds issues a new price list.
"""

# ---------------------------------------------------------------------------
# Shared image sets — king sizes are the same physical bed as the double.
# Define once, reference by name so a single update fixes all sizes.
# ---------------------------------------------------------------------------

_MONTANA_IMAGES = [
    "https://wholesalebeds.co.uk/wp-content/uploads/2022/03/MONTOTTO-ROOM-SET-e1770131185318.jpg",
    "https://wholesalebeds.co.uk/wp-content/uploads/2022/03/montana-4-01.jpg",
    "https://wholesalebeds.co.uk/wp-content/uploads/2022/03/montana-5-01.jpg",
    "https://wholesalebeds.co.uk/wp-content/uploads/2022/03/montana-3-01.jpg",
]

_HOUSTON_IMAGES = [
    "https://wholesalebeds.co.uk/wp-content/uploads/2020/01/HOUSTON-ROOMSET-01.jpg",
    "https://wholesalebeds.co.uk/wp-content/uploads/2020/01/Houston-Ottoman-Bed-lifting-01.jpg",
    "https://wholesalebeds.co.uk/wp-content/uploads/2020/01/Houston-Ottoman-Bed-Photoroom-12-01.jpg",
]

_GLASGOW_IMAGES = [
    "https://wholesalebeds.co.uk/wp-content/uploads/2023/10/upscaled.jpg",
    "https://wholesalebeds.co.uk/wp-content/uploads/2023/10/GLAS-NEW-WHITE-BACKGROUND-01.jpg",
    "https://wholesalebeds.co.uk/wp-content/uploads/2023/10/GLASGOW-OATMELA-OTTO-01.jpg",
]

_LISBON_IMAGES = [
    "https://wholesalebeds.co.uk/wp-content/uploads/2023/10/LISBON-ROOMSET-01.jpg",
    "https://wholesalebeds.co.uk/wp-content/uploads/2023/10/lisbon-otto-lift-up-01.jpg",
    "https://wholesalebeds.co.uk/wp-content/uploads/2023/10/LISBON-SMOKED-GREY-01.jpg",
]

_TEXAS_FG_IMAGES = [
    "https://wholesalebeds.co.uk/wp-content/uploads/2017/11/FGOTTO-NEW-ROOMSET.jpg",
    "https://wholesalebeds.co.uk/wp-content/uploads/2017/11/FGOTTO-WHITE-BACKGROUND.jpg",
    "https://wholesalebeds.co.uk/wp-content/uploads/2017/11/custom_image-Photoroom-3-2-01-1.jpg",
]

_TEXAS_BLACK_IMAGES = [
    "https://wholesalebeds.co.uk/wp-content/uploads/2017/11/OTTO-ROOMSET-01.jpg",
    "https://wholesalebeds.co.uk/wp-content/uploads/2017/11/OTTO-ROOMSET-01-1.jpg",
]

_TEXAS_BROWN_IMAGES = [
    "https://wholesalebeds.co.uk/wp-content/uploads/2017/11/OTTO-BROWN-ROOMSET-1.jpg",
    "https://wholesalebeds.co.uk/wp-content/uploads/2017/11/Texas-Otto-Brown-01.jpg",
]

_SMOKED_GREY_IMAGES = [
    "https://wholesalebeds.co.uk/wp-content/uploads/2023/10/SMGOTTO12.jpg",
    "https://wholesalebeds.co.uk/wp-content/uploads/2023/10/SMGOTTO-2-01.jpg",
    "https://wholesalebeds.co.uk/wp-content/uploads/2023/10/SMGOTTO-3-01.jpg",
    "https://wholesalebeds.co.uk/wp-content/uploads/2023/10/SMGOTTO-7-01.jpg",
    "https://wholesalebeds.co.uk/wp-content/uploads/2023/10/SMGOTTO-6-01.jpg",
    "https://wholesalebeds.co.uk/wp-content/uploads/2023/10/SMGOTTO-4-01.jpg",
]

# ---------------------------------------------------------------------------
# Catalog
# ---------------------------------------------------------------------------

OTTOMAN_CATALOG = [
    # ── Montana Grey Wool Effect ─────────────────────────────────────────────
    {
        "sku": "MONTOTTO46",
        "title": "Montana Ottoman Storage Bed Grey Wool Effect Fabric",
        "size": "4'6 Double",
        "colour": "Grey Wool Effect",
        "cost_gbp": 128.0,
        "delivery_gbp": 45.0,
        "description": "Full ottoman storage bed with hydraulic gas lift, high padded headboard, grey wool effect fabric.",
        "images": _MONTANA_IMAGES,
    },
    {
        "sku": "MONTOTTO50",
        "title": "Montana Ottoman Storage Bed Grey Wool Effect Fabric",
        "size": "5'0 King",
        "colour": "Grey Wool Effect",
        "cost_gbp": 133.0,
        "delivery_gbp": 45.0,
        "description": "Full ottoman storage bed with hydraulic gas lift, high padded headboard, grey wool effect fabric.",
        "images": _MONTANA_IMAGES,
    },
    # ── Houston Grey Plush Velvet ────────────────────────────────────────────
    {
        "sku": "HOUOTTO46",
        "title": "Houston Ottoman Storage Bed Grey Plush Velvet High Head End",
        "size": "4'6 Double",
        "colour": "Grey Plush Velvet",
        "cost_gbp": 159.0,
        "delivery_gbp": 45.0,
        "description": "Ottoman storage bed, grey plush velvet fabric, high head end, hydraulic gas lift.",
        "images": _HOUSTON_IMAGES,
    },
    {
        "sku": "HOUOTTO50",
        "title": "Houston Ottoman Storage Bed Grey Plush Velvet High Head End",
        "size": "5'0 King",
        "colour": "Grey Plush Velvet",
        "cost_gbp": 169.0,
        "delivery_gbp": 45.0,
        "description": "Ottoman storage bed, grey plush velvet fabric, high head end, hydraulic gas lift.",
        "images": _HOUSTON_IMAGES,
    },
    # ── Glasgow Oatmeal Linen ────────────────────────────────────────────────
    {
        "sku": "GLASOTTO46",
        "title": "Glasgow Ottoman Storage Bed Oatmeal Linen Fabric",
        "size": "4'6 Double",
        "colour": "Oatmeal Linen",
        "cost_gbp": 149.0,
        "delivery_gbp": 45.0,
        "description": "Ottoman storage bed, oatmeal linen fabric, hydraulic gas lift, neutral tone.",
        "images": _GLASGOW_IMAGES,
    },
    {
        "sku": "GLASOTTO50",
        "title": "Glasgow Ottoman Storage Bed Oatmeal Linen Fabric",
        "size": "5'0 King",
        "colour": "Oatmeal Linen",
        "cost_gbp": 159.0,
        "delivery_gbp": 45.0,
        "description": "Ottoman storage bed, oatmeal linen fabric, hydraulic gas lift, neutral tone.",
        "images": _GLASGOW_IMAGES,
    },
    # ── Lisbon Smoked Grey ───────────────────────────────────────────────────
    {
        "sku": "LISBOTTO46",
        "title": "Lisbon Ottoman Storage Bed Smoked Grey Fabric",
        "size": "4'6 Double",
        "colour": "Smoked Grey",
        "cost_gbp": 130.0,
        "delivery_gbp": 45.0,
        "description": "Ottoman storage bed, smoked grey fabric, hydraulic gas lift.",
        "images": _LISBON_IMAGES,
    },
    {
        "sku": "LISBOTTO50",
        "title": "Lisbon Ottoman Storage Bed Smoked Grey Fabric",
        "size": "5'0 King",
        "colour": "Smoked Grey",
        "cost_gbp": 140.0,
        "delivery_gbp": 45.0,
        "description": "Ottoman storage bed, smoked grey fabric, hydraulic gas lift.",
        "images": _LISBON_IMAGES,
    },
    # ── Texas Grey Fabric ────────────────────────────────────────────────────
    {
        "sku": "TEXFGOTTO46",
        "title": "Texas Ottoman Storage Bed Grey Fabric High Head End",
        "size": "4'6 Double",
        "colour": "Grey Fabric",
        "cost_gbp": 154.0,
        "delivery_gbp": 45.0,
        "description": "Ottoman storage bed, grey fabric, high head end, hydraulic gas lift.",
        "images": _TEXAS_FG_IMAGES,
    },
    {
        "sku": "TEXFGOTTO50",
        "title": "Texas Ottoman Storage Bed Grey Fabric High Head End",
        "size": "5'0 King",
        "colour": "Grey Fabric",
        "cost_gbp": 164.0,
        "delivery_gbp": 45.0,
        "description": "Ottoman storage bed, grey fabric, high head end, hydraulic gas lift.",
        "images": _TEXAS_FG_IMAGES,
    },
    # ── Texas Black Faux Leather ─────────────────────────────────────────────
    {
        "sku": "OTTOBLACK46",
        "title": "Texas Ottoman Storage Bed Black Faux Leather",
        "size": "4'6 Double",
        "colour": "Black Faux Leather",
        "cost_gbp": 144.0,
        "delivery_gbp": 45.0,
        "description": "Ottoman storage bed, black faux leather, hydraulic gas lift.",
        "images": _TEXAS_BLACK_IMAGES,
    },
    # ── Texas Brown Faux Leather ─────────────────────────────────────────────
    {
        "sku": "OTTOBROWN46",
        "title": "Texas Ottoman Storage Bed Brown Faux Leather",
        "size": "4'6 Double",
        "colour": "Brown Faux Leather",
        "cost_gbp": 144.0,
        "delivery_gbp": 45.0,
        "description": "Ottoman storage bed, brown faux leather, hydraulic gas lift.",
        "images": _TEXAS_BROWN_IMAGES,
    },
    # ── Smoked Grey High Head End ────────────────────────────────────────────
    {
        "sku": "SMGOTTO46",
        "title": "Smoked Grey Ottoman Storage Bed High Head End",
        "size": "4'6 Double",
        "colour": "Smoked Grey",
        "cost_gbp": 149.0,
        "delivery_gbp": 45.0,
        "description": "Ottoman storage bed, smoked grey fabric, high head end, hydraulic gas lift.",
        "images": _SMOKED_GREY_IMAGES,
    },
    {
        "sku": "SMGOTTO50",
        "title": "Smoked Grey Ottoman Storage Bed High Head End",
        "size": "5'0 King",
        "colour": "Smoked Grey",
        "cost_gbp": 159.0,
        "delivery_gbp": 45.0,
        "description": "Ottoman storage bed, smoked grey fabric, high head end, hydraulic gas lift.",
        "images": _SMOKED_GREY_IMAGES,
    },
    {
        "sku": "SMGOTTO60",
        "title": "Smoked Grey Ottoman Storage Bed High Head End",
        "size": "6'0 Super King",
        "colour": "Smoked Grey",
        "cost_gbp": 169.0,
        "delivery_gbp": 45.0,
        "description": "Ottoman storage bed, smoked grey fabric, high head end, hydraulic gas lift.",
        "images": _SMOKED_GREY_IMAGES,
    },
]


# ---------------------------------------------------------------------------
# Mattress catalog
# ---------------------------------------------------------------------------
# Prices sourced from Issue 15 Price List page 11 (QTY 1-7, all ex-VAT).
# Delivery is the Zone 1 DHD 2MAN rate from page 28 (£45 per mattress).
#
# NOTE: At QTY 1-7 pricing, ALL of these mattresses are unviable for eBay
# dropshipping — trade prices are 2-8× higher than current eBay UK market
# averages. Example: PINMAST double (trade £70+delivery £45, total £138 inc
# VAT) requires ~£217 min sell for 25% margin, but eBay market avg for open
# coil doubles is £77-156. REG-1000 double requires ~£400 but market avg is
# £156. These are UK-made products priced for physical retailers at QTY 8+.
#
# Kept with correct prices so the margin checker rejects them honestly rather
# than passing with fabricated low costs. If a lower-cost mattress supplier
# is found, add to a separate catalog rather than overwriting these.
#
# eBay category: 131588 (Mattresses)
# Required aspects: Firmness, Size, Type — built in lister._build_aspects().
# ---------------------------------------------------------------------------

MATTRESS_CATALOG = [
    # ── Pinemaster Open Coil (budget, medium) ───────────────────────────────
    {
        "sku": "PINMAST3FT",
        "title": "Pinemaster Open Coil Spring Mattress",
        "size": "3ft Single",
        "cost_gbp": 54.0,
        "delivery_gbp": 45.0,
        "description": "Open coil spring mattress, medium firmness, damask fabric, panel quilted, anti-allergy.",
        "images": ["https://wholesalebeds.co.uk/wp-content/uploads/2024/06/pinemaster-01.jpg"],
    },
    {
        "sku": "PINMAST46",
        "title": "Pinemaster Open Coil Spring Mattress",
        "size": "4ft6 Double",
        "cost_gbp": 70.0,
        "delivery_gbp": 45.0,
        "description": "Open coil spring mattress, medium firmness, damask fabric, panel quilted, anti-allergy.",
        "images": ["https://wholesalebeds.co.uk/wp-content/uploads/2024/06/pinemaster-01.jpg"],
    },
    {
        "sku": "PINMAST50",
        "title": "Pinemaster Open Coil Spring Mattress",
        "size": "5ft King",
        "cost_gbp": 91.0,
        "delivery_gbp": 45.0,
        "description": "Open coil spring mattress, medium firmness, damask fabric, panel quilted, anti-allergy.",
        "images": ["https://wholesalebeds.co.uk/wp-content/uploads/2024/06/pinemaster-01.jpg"],
    },
    # ── Molly Orthopaedic (open coil, firm) ─────────────────────────────────
    {
        "sku": "MOLLORTHO3FT",
        "title": "Molly Orthopaedic Open Coil Spring Mattress",
        "size": "3ft Single",
        "cost_gbp": 68.0,
        "delivery_gbp": 45.0,
        "description": "Orthopaedic open coil spring mattress, firm support, quilted border, damask fabric, anti-allergy.",
        "images": ["https://wholesalebeds.co.uk/wp-content/uploads/2024/06/molly-01.jpg"],
    },
    {
        "sku": "MOLLORTHO46",
        "title": "Molly Orthopaedic Open Coil Spring Mattress",
        "size": "4ft6 Double",
        "cost_gbp": 91.0,
        "delivery_gbp": 45.0,
        "description": "Orthopaedic open coil spring mattress, firm support, quilted border, damask fabric, anti-allergy.",
        "images": ["https://wholesalebeds.co.uk/wp-content/uploads/2024/06/molly-01.jpg"],
    },
    {
        "sku": "MOLLORTHO50",
        "title": "Molly Orthopaedic Open Coil Spring Mattress",
        "size": "5ft King",
        "cost_gbp": 106.0,
        "delivery_gbp": 45.0,
        "description": "Orthopaedic open coil spring mattress, firm support, quilted border, damask fabric, anti-allergy.",
        "images": ["https://wholesalebeds.co.uk/wp-content/uploads/2024/06/molly-01.jpg"],
    },
    # ── Regency 1000 Pocket Sprung ───────────────────────────────────────────
    {
        "sku": "REG1000-3FT",
        "title": "Regency 1000 Pocket Sprung Mattress",
        "size": "3ft Single",
        "cost_gbp": 136.0,
        "delivery_gbp": 45.0,
        "description": "1000 individually pocketed springs, medium-firm support, stretch knitted fabric, anti-allergy, air vents.",
        "images": ["https://wholesalebeds.co.uk/wp-content/uploads/2024/06/regency-01.jpg"],
    },
    {
        "sku": "REG1000-4FT",
        "title": "Regency 1000 Pocket Sprung Mattress",
        "size": "4ft Small Double",
        "cost_gbp": 167.0,
        "delivery_gbp": 45.0,
        "description": "1000 individually pocketed springs, medium-firm support, stretch knitted fabric, anti-allergy, air vents.",
        "images": ["https://wholesalebeds.co.uk/wp-content/uploads/2024/06/regency-01.jpg"],
    },
    {
        "sku": "REG1000-46",
        "title": "Regency 1000 Pocket Sprung Mattress",
        "size": "4ft6 Double",
        "cost_gbp": 167.0,
        "delivery_gbp": 45.0,
        "description": "1000 individually pocketed springs, medium-firm support, stretch knitted fabric, anti-allergy, air vents.",
        "images": ["https://wholesalebeds.co.uk/wp-content/uploads/2024/06/regency-01.jpg"],
    },
    {
        "sku": "REG1000-50",
        "title": "Regency 1000 Pocket Sprung Mattress",
        "size": "5ft King",
        "cost_gbp": 196.0,
        "delivery_gbp": 45.0,
        "description": "1000 individually pocketed springs, medium-firm support, stretch knitted fabric, anti-allergy, air vents.",
        "images": ["https://wholesalebeds.co.uk/wp-content/uploads/2024/06/regency-01.jpg"],
    },
    {
        "sku": "REG1000-60",
        "title": "Regency 1000 Pocket Sprung Mattress",
        "size": "6ft Super King",
        "cost_gbp": 243.0,
        "delivery_gbp": 45.0,
        "description": "1000 individually pocketed springs, medium-firm support, stretch knitted fabric, anti-allergy, air vents.",
        "images": ["https://wholesalebeds.co.uk/wp-content/uploads/2024/06/regency-01.jpg"],
    },
    # ── Buckingham 2000 Pocket Sprung ────────────────────────────────────────
    {
        "sku": "BUCK2000-3FT",
        "title": "Buckingham 2000 Pocket Sprung Mattress",
        "size": "3ft Single",
        "cost_gbp": 249.0,
        "delivery_gbp": 45.0,
        "description": "2000 individually wrapped pocket springs, medium-firm support, reflex foam layer, damask fabric, anti-allergy, air vents.",
        "images": ["https://wholesalebeds.co.uk/wp-content/uploads/2024/06/buckingham-01.jpg"],
    },
    {
        "sku": "BUCK2000-46",
        "title": "Buckingham 2000 Pocket Sprung Mattress",
        "size": "4ft6 Double",
        "cost_gbp": 289.0,
        "delivery_gbp": 45.0,
        "description": "2000 individually wrapped pocket springs, medium-firm support, reflex foam layer, damask fabric, anti-allergy, air vents.",
        "images": ["https://wholesalebeds.co.uk/wp-content/uploads/2024/06/buckingham-01.jpg"],
    },
    {
        "sku": "BUCK2000-50",
        "title": "Buckingham 2000 Pocket Sprung Mattress",
        "size": "5ft King",
        "cost_gbp": 392.0,
        "delivery_gbp": 45.0,
        "description": "2000 individually wrapped pocket springs, medium-firm support, reflex foam layer, damask fabric, anti-allergy, air vents.",
        "images": ["https://wholesalebeds.co.uk/wp-content/uploads/2024/06/buckingham-01.jpg"],
    },
    {
        "sku": "BUCK2000-60",
        "title": "Buckingham 2000 Pocket Sprung Mattress",
        "size": "6ft Super King",
        "cost_gbp": 454.0,
        "delivery_gbp": 45.0,
        "description": "2000 individually wrapped pocket springs, medium-firm support, reflex foam layer, damask fabric, anti-allergy, air vents.",
        "images": ["https://wholesalebeds.co.uk/wp-content/uploads/2024/06/buckingham-01.jpg"],
    },
    # ── Sand 3000 Pocket Sprung (luxury) ─────────────────────────────────────
    {
        "sku": "SAND3000-3FT",
        "title": "Sand 3000 Luxury Pocket Sprung Mattress",
        "size": "3ft Single",
        "cost_gbp": 318.0,
        "delivery_gbp": 45.0,
        "description": "3000 individually pocketed springs, reflex foam layer, damask fabric, air vents, handles, anti-allergy.",
        "images": ["https://wholesalebeds.co.uk/wp-content/uploads/2024/06/sand3000-01.jpg"],
    },
    {
        "sku": "SAND3000-4FT",
        "title": "Sand 3000 Luxury Pocket Sprung Mattress",
        "size": "4ft Small Double",
        "cost_gbp": 416.0,
        "delivery_gbp": 45.0,
        "description": "3000 individually pocketed springs, reflex foam layer, damask fabric, air vents, handles, anti-allergy.",
        "images": ["https://wholesalebeds.co.uk/wp-content/uploads/2024/06/sand3000-01.jpg"],
    },
    {
        "sku": "SAND3000-46",
        "title": "Sand 3000 Luxury Pocket Sprung Mattress",
        "size": "4ft6 Double",
        "cost_gbp": 416.0,
        "delivery_gbp": 45.0,
        "description": "3000 individually pocketed springs, reflex foam layer, damask fabric, air vents, handles, anti-allergy.",
        "images": ["https://wholesalebeds.co.uk/wp-content/uploads/2024/06/sand3000-01.jpg"],
    },
    {
        "sku": "SAND3000-50",
        "title": "Sand 3000 Luxury Pocket Sprung Mattress",
        "size": "5ft King",
        "cost_gbp": 469.0,
        "delivery_gbp": 45.0,
        "description": "3000 individually pocketed springs, reflex foam layer, damask fabric, air vents, handles, anti-allergy.",
        "images": ["https://wholesalebeds.co.uk/wp-content/uploads/2024/06/sand3000-01.jpg"],
    },
    {
        "sku": "SAND3000-60",
        "title": "Sand 3000 Luxury Pocket Sprung Mattress",
        "size": "6ft Super King",
        "cost_gbp": 545.0,
        "delivery_gbp": 45.0,
        "description": "3000 individually pocketed springs, reflex foam layer, damask fabric, air vents, handles, anti-allergy.",
        "images": ["https://wholesalebeds.co.uk/wp-content/uploads/2024/06/sand3000-01.jpg"],
    },
]


def search(keyword: str, max_price_gbp: float, category: str = "") -> list[dict]:
    """
    Keyword match against ottoman and/or mattress catalog.
    category: opportunity category key — used to restrict which catalog to search.
    Returns entries in the shape supplier.py expects from a live API.
    """
    import re as _re
    keyword_lower = keyword.lower()
    # Use only terms ≥4 chars to avoid substring false-matches (e.g. "and" in "hand-tufted")
    terms = [t for t in keyword_lower.split() if len(t) >= 4]
    if not terms:
        return []

    # Choose catalogs based on opportunity category
    if category == "mattress":
        catalogs = [MATTRESS_CATALOG]
    elif category == "furniture":
        # All WB furniture products are ottoman beds — only search if keyword
        # explicitly says 'ottoman'. This prevents 'tv unit with storage',
        # 'wardrobe', 'sofa', etc. from matching ottomans via the word 'storage'.
        if "ottoman" not in terms:
            return []
        catalogs = [OTTOMAN_CATALOG]
    else:
        catalogs = [OTTOMAN_CATALOG, MATTRESS_CATALOG]

    import math as _math
    # Size words — if the keyword names a size, the product MUST match it
    _SIZE_WORDS = {"single", "double", "king", "super"}
    keyword_sizes = [t for t in terms if t in _SIZE_WORDS]

    results = []
    for catalog in catalogs:
        for item in catalog:
            colour = item.get("colour", "")
            haystack = f"{item['title']} {item['size']} {colour} {item['description']}".lower()
            haystack_words = set(_re.findall(r'\b\w+\b', haystack))

            # Hard rule: if keyword specifies a size, the product must match it
            if keyword_sizes and not any(sz in haystack_words for sz in keyword_sizes):
                continue

            # Soft rule: at least 60% of non-size terms must match
            non_size_terms = [t for t in terms if t not in _SIZE_WORDS]
            if non_size_terms:
                match_count = sum(1 for t in non_size_terms if t in haystack_words)
                needed = _math.ceil(len(non_size_terms) * 0.6)
                if match_count < needed:
                    continue
            if item["cost_gbp"] > max_price_gbp:
                continue
            results.append({
                "supplier": "wholesalebeds",
                "product_id": item["sku"],
                "title": f"{item['title']} - {item['size']}",
                "cost_gbp": item["cost_gbp"],
                "shipping_gbp": item["delivery_gbp"],
                "delivery_days": 5,
                "images": item["images"],
                "description": item["description"],
                "uk_warehouse": True,
            })

    return results
