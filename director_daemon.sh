#!/bin/bash
# Keeps the director running: one loop, then a short rest (longer when it had nothing to do), forever. systemd restarts it.
cd /var/www/ebay-agent-2 || exit 1
while true; do
  /var/www/ebay-agent/venv/bin/python director.py >> /var/www/ebay-agent-2/director.log 2>&1
  tail -n 3000 director.log > /tmp/director.log.tmp && cat /tmp/director.log.tmp > director.log
  sleep 600
done
