#!/bin/bash
# Growth engine: research + list for N themes from growth_themes.txt (rotating daily). Safe to run by cron.
#   run_growth.sh [themes=2] [cap_per_theme=30] [start_index]
cd /var/www/ebay-agent-2
exec 9>/tmp/growth.lock; flock -n 9 || { echo "growth run already in progress"; exit 0; }
export SEASONAL_ONLY=0
PY=/var/www/ebay-agent/venv/bin/python
R="systemd-run --scope -q -p MemoryMax=1000M"
N=${1:-2}; CAP=${2:-30}
mapfile -t THEMES < <(grep -v '^#' growth_themes.txt | grep -v '^[[:space:]]*$')
TOTAL=${#THEMES[@]}
IDX=${3:-$(( ($(date +%s)/86400 * N) % TOTAL ))}
LOG=/tmp/growth_$(date +%F).log
echo "=== growth run $(date -u) themes=$N cap=$CAP start=$IDX" >> $LOG
$R $PY campaign_preflight.py >> $LOG 2>&1 || exit 1
for ((i=0; i<N; i++)); do
  T="${THEMES[$(( (IDX+i) % TOTAL ))]}"
  echo "--- THEME: $T" >> $LOG
  $R $PY campaign_discover.py "$T" --cap $CAP >> $LOG 2>&1
done
$R $PY growth_summary.py 24 >> $LOG 2>&1
echo "=== growth run done $(date -u)" >> $LOG
