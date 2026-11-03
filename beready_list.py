"""
UK 'Be Ready' campaign (launched 2026-10-22): research + list SINGLE items from the government checklist
(light, radio, power, warmth, water, hygiene, documents, first aid). Combos allowed if promising.

Usage:  python beready_list.py [--dry] [--cap N]
Run under:  systemd-run --scope -p MemoryMax=1000M venv/bin/python beready_list.py --dry
"""
import logging, os, sys
os.environ["QUOTA_RESERVE"] = "250"   # campaign may dip deeper than the agent default (600)
os.environ["SEASONAL_ONLY"] = "0"   # campaign runs list outside the Christmas-only seasonal filter
# Combos/kits allowed when promising (user OK 2026-11-03). Set EXCLUDE_COMBO=1 to restrict to single items.
from dotenv import load_dotenv
load_dotenv()

KEYWORDS = {
    "tools_hardware": ["wind up torch", "rechargeable led torch", "head torch rechargeable", "led torch high power",
                       "tactical led flashlight rechargeable", "super bright telescopic torch", "usb rechargeable flashlight 1000 lumens"],
    "garden_outdoor": ["camping lantern rechargeable", "solar lantern outdoor", "emergency foil blanket", "sleeping bag",
                       "water carrier collapsible", "water filter bottle", "jerry can water", "camping stove gas", "portable camping gas stove", "butane gas stove single burner",
                       "hand warmers reusable", "thermal sleeping bag liner"],
    "tech_accessories": ["wind up radio", "dab radio portable battery", "solar radio", "power bank 20000mah",
                         "solar power bank", "aa batteries rechargeable", "aaa batteries rechargeable", "usb battery charger"],
    "home_living": ["fleece blanket", "thermal blanket", "wool blanket", "money belt travel", "coin purse"],
    "office_stationery": ["waterproof document bag", "fireproof document bag", "document holder folder"],
    "health_devices": ["first aid kit", "digital thermometer", "weekly pill organiser"],
}

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s — %(message)s")
    dry = "--dry" in sys.argv
    cap = int(sys.argv[sys.argv.index("--cap") + 1]) if "--cap" in sys.argv else 25
    from research import find_opportunities
    import main
    opps = find_opportunities(top_n=cap * 3, only_keywords=KEYWORDS)
    print(f"\n{len(opps)} qualifying keyword(s):")
    for o in opps:
        print(f"  [{o['category']}] {o['keyword']}: £{o['avg_price_gbp']} | {o['active_listings']} competitors | {o['sold_per_week']}/wk")
    main.research_and_list(dry_run=dry, opportunities=opps, cap=cap)
