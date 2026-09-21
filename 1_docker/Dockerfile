# =============================================================================
#  InsaCloud - Image Ubuntu 22.04 des machines louées
# -----------------------------------------------------------------------------
#  Deux variantes construites à partir de ce même fichier :
#    Terminal : docker build -t insacloud_ubuntu:latest .
#    Bureau   : docker build -t insacloud_ubuntu_desktop:latest --build-arg DESKTOP=1 .
#
#  Services (gérés par supervisord, voir entrypoint.sh) :
#    22   SSH                (ssh root@<hote> -p <port>)
#    7681 terminal web ttyd  (http://<hote>:<port_terminal>)
#    6080 bureau XFCE noVNC  (http://<hote>:<port_bureau>)  — variante Bureau
#
#  Le mot de passe root est injecté à l'exécution : -e ROOT_PASSWORD=...
#  Firefox : Ubuntu ne fournit Firefox qu'en Snap (inutilisable dans Docker),
#  on utilise donc le dépôt APT officiel de Mozilla (firefox-esr).
# =============================================================================

FROM ubuntu:22.04

# Vide = terminal ; "1" = bureau graphique
ARG DESKTOP=""

LABEL maintainer="InsaCloud - Projet INSA STI 4A" \
      description="Machine Ubuntu louable (SSH + terminal web, option bureau XFCE)"

ENV DEBIAN_FRONTEND=noninteractive \
    ROOT_PASSWORD=insacloud \
    INSACLOUD_MODE=${DESKTOP:+desktop}

# -----------------------------------------------------------------------------
# 1. Socle commun : SSH, superviseur, terminal web, outils
# -----------------------------------------------------------------------------
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
        openssh-server sudo supervisor ttyd bash \
        ca-certificates curl nano vim htop iputils-ping net-tools \
 && apt-get clean && rm -rf /var/lib/apt/lists/*

# -----------------------------------------------------------------------------
# 2. Bureau graphique (uniquement si --build-arg DESKTOP=1)
# -----------------------------------------------------------------------------
RUN if [ -n "$DESKTOP" ]; then \
      install -d -m 0755 /etc/apt/keyrings \
   && curl -fsSL https://packages.mozilla.org/apt/repo-signing-key.gpg \
        -o /etc/apt/keyrings/packages.mozilla.org.asc \
   && echo "deb [signed-by=/etc/apt/keyrings/packages.mozilla.org.asc] https://packages.mozilla.org/apt mozilla main" \
        > /etc/apt/sources.list.d/mozilla.list \
   && printf 'Package: *\nPin: origin packages.mozilla.org\nPin-Priority: 1000\n' \
        > /etc/apt/preferences.d/mozilla \
   && apt-get update \
   && apt-get install -y --no-install-recommends \
        xfce4 xfce4-terminal mousepad adwaita-icon-theme \
        dbus-x11 x11-xserver-utils xauth \
        tigervnc-standalone-server tigervnc-tools \
        novnc websockify python3-numpy \
        firefox-esr fonts-dejavu-core \
   && apt-get clean && rm -rf /var/lib/apt/lists/* ; \
    fi

# -----------------------------------------------------------------------------
# 3. OpenSSH : root autorisé par mot de passe + correctif PAM des conteneurs
# -----------------------------------------------------------------------------
RUN mkdir -p /run/sshd \
 && sed -i 's/^#\?PermitRootLogin .*/PermitRootLogin yes/' /etc/ssh/sshd_config \
 && sed -i 's/^#\?PasswordAuthentication .*/PasswordAuthentication yes/' /etc/ssh/sshd_config \
 && sed -i 's@session\s*required\s*pam_loginuid.so@session optional pam_loginuid.so@g' /etc/pam.d/sshd

# -----------------------------------------------------------------------------
# 4. Message d'accueil + script de démarrage commun
# -----------------------------------------------------------------------------
RUN printf '%s\n' \
    '=========================================================' \
    '   Bienvenue sur votre machine InsaCloud (Ubuntu 22.04)  ' \
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
