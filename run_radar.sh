#!/bin/bash
# Market radar run (cron). Usage: run_radar.sh [budget] [--digest]
# One run at a time; the radar itself never takes the shared eBay quota below RADAR_QUOTA_FLOOR.
cd /var/www/ebay-agent-2 || exit 1
exec 8>/tmp/radar.lock
flock -n 8 || exit 0
BUDGET=${1:-500}
shift
/var/www/ebay-agent/venv/bin/python market_radar.py run --budget "$BUDGET" "$@" >> /var/www/ebay-agent-2/radar.log 2>&1
tail -n 4000 /var/www/ebay-agent-2/radar.log > /tmp/radar.log.tmp && cat /tmp/radar.log.tmp > /var/www/ebay-agent-2/radar.log
