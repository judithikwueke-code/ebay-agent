#!/bin/bash
# Deploy latest code to Yagazie server and restart services.
# Run this from any machine: bash deploy.sh
# Requires SSH access to root@204.168.194.157

set -e
SERVER="root@204.168.194.157"
REMOTE_PATH="/var/www/ebay-agent"

echo "Syncing code to Yagazie..."
rsync -avz --progress \
  --exclude='__pycache__' \
  --exclude='*.pyc' \
  --exclude='*.log' \
  --exclude='ebay_agent.db' \
  --exclude='agent.db' \
  --exclude='.env' \
  --exclude='venv/' \
  "$(dirname "$0")/" \
  "$SERVER:$REMOTE_PATH/"

echo "Restarting services..."
ssh "$SERVER" "
  cd $REMOTE_PATH
  venv/bin/pip install -r requirements.txt -q
  systemctl restart ebay-agent
  systemctl restart terapeak-server
  sleep 3
  echo '=== Service status ==='
  systemctl is-active ebay-agent && echo 'ebay-agent: RUNNING'
  systemctl is-active terapeak-server && echo 'terapeak-server: RUNNING'
  echo '=== Last 5 log lines ==='
  journalctl -u ebay-agent --no-pager -n 5
"
echo "Deploy complete."
