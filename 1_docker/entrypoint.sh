#!/bin/bash
# =============================================================================
#  entrypoint.sh - Script de démarrage commun aux images InsaCloud
#  (Ubuntu, Debian, Alpine — mode "terminal" ou "desktop")
# -----------------------------------------------------------------------------
#  Démarrage (CMD) :
#    1. mot de passe root (ROOT_PASSWORD) et, si fournie, clé publique SSH
#       (SSH_PUBKEY) -> l'authentification SSH par mot de passe est alors coupée
#    2. configuration supervisord selon le mode :
#         terminal : sshd (22) + ttyd, terminal web (7681)
#         desktop  : + Xvnc (5901, interne) + XFCE + noVNC (6080)
#       Si /tls/cert.pem et /tls/key.pem sont montés, ttyd et noVNC parlent TLS.
#    3. supervisord en PID 1 (relance chaque service ; Docker relance le
#       conteneur si supervisord meurt, grâce à --restart=always)
#
#  Rotation du mot de passe à chaud (utilisé par la plateforme) :
#    docker exec <conteneur> /usr/local/bin/entrypoint.sh setpass <nouveau>
# =============================================================================
set -e

: "${ROOT_PASSWORD:=insacloud}"
: "${INSACLOUD_MODE:=terminal}"
: "${VNC_RESOLUTION:=1280x800}"
: "${SSH_PUBKEY:=}"

CONF=/etc/supervisord.insacloud.conf
TLS_CERT=/tls/cert.pem
TLS_KEY=/tls/key.pem

# --- Fonctions ----------------------------------------------------------------

# Applique un mot de passe root pour SSH ; VNC = 8 premiers caractères (limite du protocole)
apply_password() {
    local pwd="$1"
    echo "root:${pwd}" | chpasswd
    if [ "$INSACLOUD_MODE" = "desktop" ]; then
        mkdir -p /root/.vnc
        local vncpasswd; vncpasswd="$(command -v tigervncpasswd || command -v vncpasswd)"
        printf '%s' "${pwd:0:8}" | "$vncpasswd" -f > /root/.vnc/passwd
        chmod 600 /root/.vnc/passwd
    fi
}

# Options de ttyd. L'authentification est faite par `login` DANS le terminal
# (invite "login:" puis "Password:", comme une console), et non par une boîte
# de dialogue HTTP du navigateur. TLS si un certificat est monté.
ttyd_options() {
    local opts="-p 7681 -t titleFixed=InsaCloud -t fontSize=15 -t disableLeaveAlert=true"
    # -W (écriture) n'existe qu'à partir de ttyd 1.7
    if ttyd --help 2>&1 | grep -q -- '--writable'; then opts="$opts -W"; fi
    if [ -r "$TLS_CERT" ] && [ -r "$TLS_KEY" ]; then
        opts="$opts --ssl --ssl-cert $TLS_CERT --ssl-key $TLS_KEY"
    fi
    echo "$opts"
}

# --- Sous-commande : rotation du mot de passe -------------------------------
if [ "$1" = "setpass" ]; then
    [ -n "$2" ] || { echo "usage: entrypoint.sh setpass <mot_de_passe>" >&2; exit 2; }
    apply_password "$2"
    # SSH et le terminal web (`login`) lisent /etc/shadow ; VNC relit son fichier : rien à relancer.
    echo "mot de passe mis à jour (SSH, terminal web, VNC)"
    exit 0
fi

# --- 1. Mot de passe root + clé SSH -----------------------------------------
apply_password "$ROOT_PASSWORD"
ssh-keygen -A >/dev/null 2>&1 || true          # clés hôte SSH si absentes

if [ -n "$SSH_PUBKEY" ]; then
    # Clé publique fournie par l'utilisateur : accès SSH par clé uniquement
    mkdir -p /root/.ssh && chmod 700 /root/.ssh
    printf '%s\n' "$SSH_PUBKEY" > /root/.ssh/authorized_keys
    chmod 600 /root/.ssh/authorized_keys
    sed -i 's/^#\?PasswordAuthentication .*/PasswordAuthentication no/' /etc/ssh/sshd_config
    sed -i 's/^#\?PermitRootLogin .*/PermitRootLogin prohibit-password/' /etc/ssh/sshd_config
fi

# --- 2. Configuration supervisord ---------------------------------------------
# Bannière de l'invite de connexion du terminal web
printf 'InsaCloud - %s\nIdentifiant : root  (mot de passe : voir le coffre du site)\n\n' "$(hostname)" > /etc/issue
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
command=$(command -v ttyd) $(ttyd_options) /bin/sh -c "cat /etc/issue; exec $(command -v login)"
priority=20
autorestart=true
stdout_logfile=/dev/null
stderr_logfile=/dev/null
SUPERVISOR

# --- 3. Bureau graphique (mode desktop uniquement) ----------------------------
if [ "$INSACLOUD_MODE" = "desktop" ]; then
    mkdir -p /tmp/runtime-root && chmod 700 /tmp/runtime-root
    NOVNC_DIR=/usr/share/novnc
    [ -f /usr/share/webapps/novnc/vnc.html ] && NOVNC_DIR=/usr/share/webapps/novnc
    ln -sf "$NOVNC_DIR/vnc.html" "$NOVNC_DIR/index.html" 2>/dev/null || true
    NOVNC_TLS=""
    if [ -r "$TLS_CERT" ] && [ -r "$TLS_KEY" ]; then
        NOVNC_TLS="--cert $TLS_CERT --key $TLS_KEY --ssl-only"
    fi

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
command=$(command -v websockify) $NOVNC_TLS --web $NOVNC_DIR 6080 localhost:5901
priority=50
autorestart=true
stdout_logfile=/dev/null
stderr_logfile=/dev/null
SUPERVISOR
fi

# `supervisorctl status` doit marcher sans option sur toutes les distributions
ln -sf /run/supervisord.sock /var/run/supervisor.sock 2>/dev/null || true

# Le mot de passe ne doit pas rester lisible dans l'environnement des processus
unset ROOT_PASSWORD SSH_PUBKEY

exec "$(command -v supervisord)" -c "$CONF"
