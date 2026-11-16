#!/bin/bash
# High-value discovery for the profit-per-day target: items GBP 80-450 that earn at least GBP 15 each (cheapest-quarter price).
#   run_premium_plus.sh [cap=10]
# One browser at a time: queues behind any growth / premium run on the shared lock.
cd /var/www/ebay-agent-2
exec 9>/tmp/growth.lock; flock -w 5400 9 || { echo "lock busy - not started"; exit 1; }
export SEASONAL_ONLY=0 PREMIUM_BAND="80-450" PREMIUM_MIN_PROFIT=15 PREMIUM_MIN_SOLD=1.0
PY=/var/www/ebay-agent/venv/bin/python
R="systemd-run --scope -q -p MemoryMax=1000M"
CAP=${1:-10}
LOG=/tmp/growth_$(date +%F).log
echo "=== premium PLUS run $(date -u) cap=$CAP band=$PREMIUM_BAND min_profit=$PREMIUM_MIN_PROFIT" >> $LOG
$R $PY campaign_preflight.py >> $LOG 2>&1 || exit 1
T="High-value UK winter and Christmas products where eBay buyers pay GBP 80-450 (single items, not bundles): heated clothes airers, dehumidifiers, oil-filled radiators and electric heaters, robot vacuums, air fryers and multi-cookers, pressure washers, cordless drill and tool kits, dash cams, projectors, massage guns and chairs, treadmills and walking pads, portable saunas, air purifiers, patio heaters, pre-lit artificial Christmas trees, outdoor light-up Christmas figures, gaming chairs, standing desks, steam mops, food processors, coffee machines"
echo "--- THEME (PREMIUM PLUS): $T" >> $LOG
$R $PY campaign_discover.py "$T" --cap $CAP --premium >> $LOG 2>&1
$R $PY growth_summary.py 24 >> $LOG 2>&1
echo "=== premium PLUS run done $(date -u)" >> $LOG
