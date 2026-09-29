#!/bin/bash
# =============================================================================
#  run_local.sh - Lance InsaCloud sur ce poste (développement / démo)
#  Pré-requis : Docker actif (colima start) et les images construites.
#  Base de données : 2_webapp/insacloud.db (créée si absente).
# =============================================================================
cd "$(dirname "$0")"
if [ ! -x .venv/bin/python ]; then
  python3 -m venv .venv && .venv/bin/pip install -q flask cryptography
fi
export INSACLOUD_PORT="${INSACLOUD_PORT:-5055}"     # 5000 est pris par AirPlay sur macOS
export INSACLOUD_EMBED_FAUCHEUR=1                   # Faucheur en thread (pas de systemd ici)
export INSACLOUD_FAUCHEUR_INTERVAL=5
# 127.0.0.1 plutôt que localhost : ce dernier peut résoudre en ::1 alors que
# l'application écoute en IPv4, ce qui donne un refus de connexion trompeur.
echo "InsaCloud : http://127.0.0.1:${INSACLOUD_PORT}"
exec .venv/bin/python app.py
