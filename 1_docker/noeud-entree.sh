#!/bin/sh
# =============================================================================
#  Démarrage d'un nœud : autorise le contrôleur, ouvre l'accès au démon Docker,
#  lance l'agent d'inscription, puis sshd au premier plan.
# =============================================================================
set -eu

CLES=${INSACLOUD_CLES:-/cles}

# --- 1. La clé publique du contrôleur devient la seule autorisée -------------
if [ ! -f "$CLES/id_ed25519.pub" ]; then
    echo "Clé publique du contrôleur absente ($CLES/id_ed25519.pub) : le nœud" >&2
    echo "serait injoignable. Démarrez le service « cles » d'abord." >&2
    exit 1
fi
install -o insacloud -g insacloud -m 600 \
        "$CLES/id_ed25519.pub" /home/insacloud/.ssh/authorized_keys

# --- 2. Accès au démon Docker ------------------------------------------------
# Le compte de service doit appartenir au groupe propriétaire de la socket.
# Ce groupe n'existe pas forcément dans l'image : on le crée avec le bon GID.
if [ -S /var/run/docker.sock ]; then
    GID_SOCKET=$(stat -c %g /var/run/docker.sock)
    if ! getent group "$GID_SOCKET" >/dev/null 2>&1; then
        addgroup -g "$GID_SOCKET" socketdocker
    fi
    GROUPE=$(getent group "$GID_SOCKET" | cut -d: -f1)
    adduser insacloud "$GROUPE" >/dev/null
    echo "Nœud ${INSACLOUD_WORKER_NAME:-?} : socket Docker accessible (groupe $GROUPE)."
else
    echo "Nœud ${INSACLOUD_WORKER_NAME:-?} : aucune socket Docker montée." >&2
fi

# --- 3. L'agent s'inscrit auprès du contrôleur et bat le cœur ----------------
if [ -n "${INSACLOUD_AGENT_TOKEN:-}" ]; then
    python3 /opt/insacloud/worker_agent.py &
else
    echo "INSACLOUD_AGENT_TOKEN absent : le nœud ne s'inscrira pas." >&2
fi

# --- 4. sshd au premier plan --------------------------------------------------
# « exec » : sshd devient PID 1 et reçoit les signaux. « docker stop » coupe
# donc le nœud net, ce qui est exactement la panne que l'on veut démontrer.
exec /usr/sbin/sshd -D -e
