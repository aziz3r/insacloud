# =============================================================================
#  InsaCloud - Image Alpine Linux 3.20 des machines louées
# -----------------------------------------------------------------------------
#    Terminal : docker build -t insacloud_alpine:latest -f alpine.Dockerfile .
#    Bureau   : docker build -t insacloud_alpine_desktop:latest --build-arg DESKTOP=1 -f alpine.Dockerfile .
#  Ports : 22 SSH · 7681 terminal web · 6080 bureau (variante Bureau)
#  Avantage : ~40 Mo en mode terminal, démarrage quasi instantané.
# =============================================================================

FROM alpine:3.20

ARG DESKTOP=""

LABEL maintainer="InsaCloud - Projet INSA STI 4A" \
      description="Machine Alpine louable (SSH + terminal web, option bureau XFCE)"

ENV ROOT_PASSWORD=insacloud \
    INSACLOUD_MODE=${DESKTOP:+desktop}

# 1. Socle commun (apk = gestionnaire de paquets d'Alpine ; --no-cache = image légère)
RUN apk add --no-cache openssh supervisor ttyd bash curl nano htop

# 2. Bureau graphique (si DESKTOP=1) — Firefox ESR est natif sur Alpine
RUN if [ -n "$DESKTOP" ]; then \
      apk add --no-cache \
        xfce4 xfce4-terminal mousepad adwaita-icon-theme \
        dbus-x11 xauth mesa-dri-gallium \
        tigervnc novnc websockify \
        firefox-esr font-dejavu ; \
    fi

# 3. OpenSSH + shell bash pour root
RUN ssh-keygen -A \
 && sed -i 's/^#\?PermitRootLogin .*/PermitRootLogin yes/' /etc/ssh/sshd_config \
 && sed -i 's/^#\?PasswordAuthentication .*/PasswordAuthentication yes/' /etc/ssh/sshd_config \
 && sed -i 's#^\(root:.*:\)/bin/[a-z]*$#\1/bin/bash#' /etc/passwd

# 4. MOTD + entrypoint
RUN printf '%s\n' \
    '=========================================================' \
    '   Bienvenue sur votre machine InsaCloud (Alpine Linux)  ' \
    '   Cette machine est louee pour une duree limitee.       ' \
    '   Elle sera detruite automatiquement a expiration.      ' \
    '=========================================================' \
    > /etc/motd

# `login` (terminal web) doit accepter root sur un pseudo-terminal : pas de liste securetty
RUN rm -f /etc/securetty

COPY entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh

EXPOSE 22 7681 6080
CMD ["/usr/local/bin/entrypoint.sh"]
