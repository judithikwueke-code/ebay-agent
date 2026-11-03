#!/bin/bash
# Be Ready campaign: preflight, user-picked products, supplied keywords, then self-discovered keywords.
cd /var/www/ebay-agent-2
export SEASONAL_ONLY=0
PY=/var/www/ebay-agent/venv/bin/python
R="systemd-run --scope -q -p MemoryMax=1000M"
$R $PY campaign_preflight.py > /tmp/campaign_preflight.log 2>&1 || { echo PREFLIGHT_FAILED > /tmp/campaign_done.flag; exit 1; }
$R $PY seed_amazon.py B0D798PJJ9 "emergency survival kit" garden_outdoor > /tmp/campaign_seed1.log 2>&1
$R $PY seed_amazon.py B0746GHQ2N "mini first aid kit" health_devices > /tmp/campaign_seed2.log 2>&1
$R $PY beready_list.py --cap 25 > /tmp/beready_live2.log 2>&1
$R $PY campaign_discover.py "UK government Be Ready campaign (launched 22 Oct 2026): households told to prepare for 3 days of power cuts, water/food disruption, cyber outages; winter approaching" --cap 40 --skip "wind up torch,solar power bank,wind up radio,solar radio,jerry can water" > /tmp/campaign_discover.log 2>&1
echo ALLDONE > /tmp/campaign_done.flag
