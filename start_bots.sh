#!/bin/bash
# Auto-start bots after VPS reboot
cd "$(dirname "$0")"

# Alert: VPS just rebooted
source .env 2>/dev/null
if [ -n "$TG_BOT_TOKEN" ] && [ -n "$TG_CHAT_ID" ]; then
    curl -s -X POST "https://api.telegram.org/bot${TG_BOT_TOKEN}/sendMessage" \
        -d chat_id="$TG_CHAT_ID" \
        -d text="⚠️ VPS rebooted — restarting bots..." > /dev/null 2>&1
fi

# Wait for network
sleep 5

# Ensure output dirs exist
mkdir -p analysis/output/pairs_data analysis/output_live/pairs_data

# Paper bot (:8097, no Telegram)
TG_BOT_TOKEN= TG_CHAT_ID= \
    nohup .venv/bin/python3 -m analysis.reversal > analysis/output/reversal_v10.log 2>&1 &
echo "Paper bot started (PID: $!)"

# Live bot (:8098)
HL_MODE=live HL_CAPITAL=254.92 WEB_PORT=8098 HL_OUTPUT_DIR=analysis/output_live \
    nohup .venv/bin/python3 -m analysis.reversal > analysis/output_live/reversal_v10.log 2>&1 &
echo "Live bot started (PID: $!)"

# Admin panel (:8090)
nohup .venv/bin/python3 admin.py > analysis/output/admin.log 2>&1 &
echo "Admin panel started (PID: $!)"
