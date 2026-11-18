#!/bin/bash
# One discovery hunt for the director.   run_hunt.sh "<theme>" <cap> <band e.g. 80-450> <min profit> <min sold/week>
# One browser at a time (shared lock). Never starts if memory is short.
cd /var/www/ebay-agent-2 || exit 1
exec 9>/tmp/growth.lock; flock -n 9 || { echo "lock busy - not started"; exit 3; }
T="$1"; CAP=${2:-8}; BAND=${3:-80-450}; PROFIT=${4:-12}; SOLD=${5:-1.0}
export SEASONAL_ONLY=0 PREMIUM_BAND="$BAND" PREMIUM_MIN_PROFIT="$PROFIT" PREMIUM_MIN_SOLD="$SOLD"
PY=/var/www/ebay-agent/venv/bin/python
R="systemd-run --scope -q -p MemoryMax=1300M"
LOG=/tmp/growth_$(date +%F).log
echo "=== director hunt $(date -u) cap=$CAP band=$BAND min_profit=$PROFIT" >> $LOG
echo "--- THEME (DIRECTOR): $T" >> $LOG
$R $PY campaign_preflight.py >> $LOG 2>&1 || { echo "preflight failed"; exit 2; }
$R $PY campaign_discover.py "$T" --cap $CAP --premium >> $LOG 2>&1
echo "=== director hunt done $(date -u)" >> $LOG
