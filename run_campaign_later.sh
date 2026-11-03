#!/bin/bash
# Wait for the eBay Browse API quota reset (00:00 US Pacific = 08:00 UTC), then run the campaign.
target=$(date -u -d "2026-11-04 08:05" +%s)
while [ "$(date -u +%s)" -lt "$target" ]; do sleep 120; done
exec /var/www/ebay-agent-2/run_campaign.sh
