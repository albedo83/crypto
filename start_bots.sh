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

# Paper bot (:8097, no Telegram) — served behind nginx at /paper/
TG_BOT_TOKEN= TG_CHAT_ID= HL_ROOT_PATH=/paper \
    nohup .venv/bin/python3 -m analysis.reversal > analysis/output/reversal_v10.log 2>&1 &
echo "Paper bot started (PID: $!)"

# Live bot (:8098) — served behind nginx at /bot/
HL_MODE=live HL_CAPITAL=300 WEB_PORT=8098 HL_OUTPUT_DIR=analysis/output_live HL_ROOT_PATH=/bot \
    nohup .venv/bin/python3 -m analysis.reversal > analysis/output_live/reversal_v10.log 2>&1 &
echo "Live bot started (PID: $!)"

# Bot 2 (:8099) — paper mode until private key is set.
# No HL_ROOT_PATH: no nginx /bot2/ location configured, direct-port access only.
# To switch to live: add HL_MODE=live HL_PRIVATE_KEY=<KEY> HL_CAPITAL=<amount>
TG_BOT_TOKEN= TG_CHAT_ID= WEB_PORT=8099 HL_OUTPUT_DIR=analysis/output_live2 \
    nohup .venv/bin/python3 -m analysis.reversal > analysis/output_live2/reversal_v10.log 2>&1 &
echo "Bot 2 started (PID: $!)"

# Admin panel (:8090) — served behind nginx at /crypto/
ADMIN_ROOT_PATH=/crypto nohup .venv/bin/python3 admin.py > analysis/output/admin.log 2>&1 &
echo "Admin panel started (PID: $!)"
