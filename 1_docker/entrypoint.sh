#!/bin/bash
# =============================================================================
#  entrypoint.sh - Script de démarrage commun aux images InsaCloud
#  (Ubuntu, Debian, Alpine — mode "terminal" ou "desktop")
# -----------------------------------------------------------------------------
#  1. Applique le mot de passe root transmis par la plateforme (ROOT_PASSWORD)
#  2. Génère la configuration supervisord selon le mode :
#       terminal : sshd (22) + ttyd, terminal web (7681)
#       desktop  : + Xvnc (5901, interne) + XFCE + noVNC (6080)
#  3. Lance supervisord en PID 1 : chaque service est relancé s'il plante,
#     et si supervisord lui-même meurt, Docker relance le conteneur
#     grâce à --restart=always.
# =============================================================================
set -e

: "${ROOT_PASSWORD:=insacloud}"
: "${INSACLOUD_MODE:=terminal}"
: "${VNC_RESOLUTION:=1280x800}"

# --- 1. Mot de passe root (SSH + terminal web) --------------------------------
echo "root:${ROOT_PASSWORD}" | chpasswd
ssh-keygen -A >/dev/null 2>&1 || true          # clés hôte SSH si absentes

# --- 2. Terminal web : ttyd protège l'accès par le mot de passe root ---------
#     (l'option -W "writable" n'existe qu'à partir de ttyd 1.7)
TTYD_OPTS="-p 7681 -c root:${ROOT_PASSWORD} -t titleFixed=InsaCloud -t fontSize=15"
if ttyd --help 2>&1 | grep -q -- '--writable'; then TTYD_OPTS="$TTYD_OPTS -W"; fi
LOGIN_SHELL="$(command -v bash || echo /bin/sh)"

CONF=/etc/supervisord.insacloud.conf
cat > "$CONF" <<SUPERVISOR
[supervisord]
nodaemon=true
user=root
logfile=/dev/null
logfile_maxbytes=0
pidfile=/run/supervisord.pid

[unix_http_server]
file=/run/supervisord.sock

[supervisorctl]
serverurl=unix:///run/supervisord.sock

[rpcinterface:supervisor]
supervisor.rpcinterface_factory = supervisor.rpcinterface:make_main_rpcinterface

[program:sshd]
command=/usr/sbin/sshd -D -e
priority=10
autorestart=true
stdout_logfile=/dev/stdout
stdout_logfile_maxbytes=0
stderr_logfile=/dev/stderr
stderr_logfile_maxbytes=0

[program:ttyd]
command=$(command -v ttyd) $TTYD_OPTS $LOGIN_SHELL -l
priority=20
autorestart=true
stdout_logfile=/dev/null
stderr_logfile=/dev/null
SUPERVISOR

# --- 3. Bureau graphique (mode desktop uniquement) ----------------------------
if [ "$INSACLOUD_MODE" = "desktop" ]; then
    mkdir -p /root/.vnc /tmp/runtime-root && chmod 700 /tmp/runtime-root
    # Mot de passe VNC = 8 premiers caractères (limite du protocole VNC)
    VNCPASSWD="$(command -v tigervncpasswd || command -v vncpasswd)"
    printf '%s' "${ROOT_PASSWORD:0:8}" | "$VNCPASSWD" -f > /root/.vnc/passwd
    chmod 600 /root/.vnc/passwd
    # Racine web de noVNC (chemin différent selon la distribution)
    NOVNC_DIR=/usr/share/novnc
    [ -f /usr/share/webapps/novnc/vnc.html ] && NOVNC_DIR=/usr/share/webapps/novnc
    ln -sf "$NOVNC_DIR/vnc.html" "$NOVNC_DIR/index.html" 2>/dev/null || true

    cat >> "$CONF" <<SUPERVISOR

[program:xvnc]
command=$(command -v Xvnc) :1 -geometry ${VNC_RESOLUTION} -depth 24 -rfbport 5901 -localhost -SecurityTypes VncAuth -PasswordFile /root/.vnc/passwd -AlwaysShared
priority=30
autorestart=true
stdout_logfile=/dev/null
stderr_logfile=/dev/null

[program:xfce]
command=$(command -v startxfce4)
environment=DISPLAY=":1",HOME="/root",USER="root",XDG_RUNTIME_DIR="/tmp/runtime-root"
priority=40
autorestart=true
startsecs=3
stdout_logfile=/dev/null
stderr_logfile=/dev/null

[program:novnc]
command=$(command -v websockify) --web $NOVNC_DIR 6080 localhost:5901
priority=50
autorestart=true
stdout_logfile=/dev/null
stderr_logfile=/dev/null
SUPERVISOR
fi

# `supervisorctl status` doit marcher sans option sur toutes les distributions
# (Debian/Ubuntu cherchent /var/run/supervisor.sock, Alpine /run/supervisord.sock)
ln -sf /run/supervisord.sock /var/run/supervisor.sock 2>/dev/null || true

exec "$(command -v supervisord)" -c "$CONF"
