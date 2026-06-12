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

# Paper bot legacy (:8097) — DÉCOMMISSIONNÉ le 2026-06-12. Remplacé par le bot
# "paper" d'Alfred (alfred/bots.json, :8101/bot/paper). Le tracker paper-vs-BT
# lit désormais l'état Alfred paper. Ne pas relancer.
# TG_BOT_TOKEN= TG_CHAT_ID= HL_ROOT_PATH=/paper CANDLE_FETCH_SLEEP=1.0 \
#     nohup .venv/bin/python3 -m analysis.reversal > analysis/output/reversal_v10.log 2>&1 &
# echo "Paper bot started (PID: $!)"

# Live bot legacy (:8098) — MIGRÉ VERS ALFRED le 2026-06-10 (bot "live" dans
# alfred/bots.json, capital = equity $680.58, état legacy archivé dans
# analysis/output_live/). Ne pas relancer : même clé HL_PRIVATE_KEY que le
# bot Alfred live → double-trading + conflits de nonce.
# HL_MODE=live HL_CAPITAL=300 WEB_PORT=8098 HL_OUTPUT_DIR=analysis/output_live HL_ROOT_PATH=/bot \
#     BOT_PUBLIC_URL=https://echonym.fr/bot \
#     nohup .venv/bin/python3 -m analysis.reversal > analysis/output_live/reversal_v10.log 2>&1 &
# echo "Live bot started (PID: $!)"

# Junior bot legacy (:8099) — MIGRÉ VERS ALFRED le 2026-06-11 (bot "junior"
# dans alfred/bots.json, capital = equity $332.76 au reset, 0 position à la
# bascule). Ne pas relancer : même clé signataire JUNIOR_HL_PRIVATE_KEY que
# le bot Alfred → double-trading + conflits de nonce.
# TG_BOT_TOKEN="$JUNIOR_TG_BOT_TOKEN" TG_CHAT_ID="$JUNIOR_TG_CHAT_ID" \
#     TG_CATEGORIES="trade,daily,system" \
#     DASHBOARD_USER="$JUNIOR_USER" DASHBOARD_PASS="$JUNIOR_PASS" \
#     HL_PRIVATE_KEY="$JUNIOR_HL_PRIVATE_KEY" HL_MODE=live \
#     HL_ACCOUNT_ADDRESS=0xb65d5e52f229B1dAA6534034d7805A82dB7956Fe \
#     BOT_LABEL="JUNIOR" BOT_LABEL_COLOR="#3fb950" \
#     HL_CAPITAL=0 WEB_PORT=8099 HL_OUTPUT_DIR=analysis/output_live2 HL_ROOT_PATH=/junior \
#     BOT_PUBLIC_URL=https://echonym.fr/junior \
#     nohup .venv/bin/python3 -m analysis.reversal > analysis/output_live2/reversal_v10.log 2>&1 &
# echo "Junior bot started (PID: $!)"

# Apprenti bot (:8100) — INACTIF. Pour activer :
# 1) Renseigner les APPRENTI_* dans .env (suivre docs/setup_new_bot.md)
# 2) Dé-commenter le bloc ci-dessous
# 3) Ajuster HL_CAPITAL=N (montant USDC réellement déposé sur le master wallet)
# 4) Restart : fuser -k 8100/tcp puis ./start_bots.sh
# 5) Optionnel : updater watchdog cron pour seuil 4 au lieu de 3
# 6) Pause par défaut au premier boot recommandée (curl POST /api/pause)
#
# mkdir -p analysis/output_apprenti/pairs_data
# TG_BOT_TOKEN="$APPRENTI_TG_BOT_TOKEN" TG_CHAT_ID="$APPRENTI_TG_CHAT_ID" \
#     TG_CATEGORIES="trade,daily,system" \
#     DASHBOARD_USER="$APPRENTI_USER" DASHBOARD_PASS="$APPRENTI_PASS" \
#     HL_PRIVATE_KEY="$APPRENTI_HL_PRIVATE_KEY" HL_MODE=live \
#     HL_ACCOUNT_ADDRESS="$APPRENTI_MASTER_ADDRESS" \
#     BOT_LABEL="APPRENTI" BOT_LABEL_COLOR="#a371f7" \
#     HL_CAPITAL=0 WEB_PORT=8100 HL_OUTPUT_DIR=analysis/output_apprenti HL_ROOT_PATH=/apprenti \
#     BOT_PUBLIC_URL=https://echonym.fr/apprenti CANDLE_FETCH_SLEEP=1.0 \
#     nohup .venv/bin/python3 -m analysis.reversal > analysis/output_apprenti/reversal_v10.log 2>&1 &
# echo "Apprenti bot started (PID: $!)"

# Admin panel legacy (:8090) — DÉCOMMISSIONNÉ le 2026-06-12. Remplacé par la
# page /master d'Alfred (:8101). Ne pas relancer.
# ADMIN_ROOT_PATH=/crypto nohup .venv/bin/python3 admin.py > analysis/output/admin.log 2>&1 &
# echo "Admin panel started (PID: $!)"

# Alfred (:8101) — la refacto : MarketDataMaster + bots (alfred/bots.json).
# Le lock alfred/data/alfred.lock fail-bind proprement si déjà lancé
# (même pattern que le double-launch Junior). Logs dans alfred/data/alfred.log
# via logging interne ; nohup capte le stdout résiduel d'uvicorn.
# ALFRED_ROOT_PATH=/alfred : préfixe les redirects (login/logout) pour nginx —
# sans lui, le middleware redirige vers https://domaine/login → 404.
ALFRED_ROOT_PATH=/alfred nohup .venv/bin/python3 -m alfred > alfred/data/alfred_stdout.log 2>&1 &
echo "Alfred started (PID: $!)"
