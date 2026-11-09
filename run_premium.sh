#!/bin/bash
# High-ticket ("premium") discovery run: GBP 40-450 items such as portable saunas, walking pads, hot tubs.
#   run_premium.sh [cap=10]
# Waits (queues) behind any growth run so two browsers never compete for the small server.
cd /var/www/ebay-agent-2
exec 9>/tmp/growth.lock; flock 9
export SEASONAL_ONLY=0
PY=/var/www/ebay-agent/venv/bin/python
R="systemd-run --scope -q -p MemoryMax=1000M"
CAP=${1:-10}
LOG=/tmp/growth_$(date +%F).log
echo "=== premium run $(date -u) cap=$CAP" >> $LOG
$R $PY campaign_preflight.py >> $LOG 2>&1 || exit 1
T="High-ticket UK home wellness, fitness and comfort products (GBP 50-400): portable steam saunas, infrared saunas, massage chairs, walking pads and treadmills, exercise bikes, hot tubs, air purifiers, heaters, projectors - winter, New Year resolutions and Christmas-gift demand"
echo "--- THEME (PREMIUM): $T" >> $LOG
$R $PY campaign_discover.py "$T" --cap $CAP --premium --manual campaign_keywords_premium.json >> $LOG 2>&1
$R $PY growth_summary.py 24 >> $LOG 2>&1
echo "=== premium run done $(date -u)" >> $LOG
