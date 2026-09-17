# =============================================================================
#  InsaCloud - Image Debian 12 des machines louées
# -----------------------------------------------------------------------------
#    Terminal : docker build -t insacloud_debian:latest -f debian.Dockerfile .
#    Bureau   : docker build -t insacloud_debian_desktop:latest --build-arg DESKTOP=1 -f debian.Dockerfile .
#  Ports : 22 SSH · 7681 terminal web · 6080 bureau (variante Bureau)
#  Debian fournit firefox-esr directement dans ses dépôts.
# =============================================================================

FROM debian:bookworm-slim

ARG DESKTOP=""

LABEL maintainer="InsaCloud - Projet INSA STI 4A" \
      description="Machine Debian louable (SSH + terminal web, option bureau XFCE)"

ENV DEBIAN_FRONTEND=noninteractive \
    ROOT_PASSWORD=insacloud \
    INSACLOUD_MODE=${DESKTOP:+desktop}

# 1. Socle commun (ttyd n'est pas dans les dépôts Debian 12 : binaire statique officiel)
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
        openssh-server sudo supervisor bash \
        ca-certificates curl nano vim htop iputils-ping procps \
 && apt-get clean && rm -rf /var/lib/apt/lists/* \
 && case "$(dpkg --print-architecture)" in arm64) T=aarch64 ;; *) T=x86_64 ;; esac \
 && curl -fsSL -o /usr/local/bin/ttyd "https://github.com/tsl0922/ttyd/releases/latest/download/ttyd.$T" \
 && chmod +x /usr/local/bin/ttyd

# 2. Bureau graphique (si DESKTOP=1)
RUN if [ -n "$DESKTOP" ]; then \
      apt-get update \
   && apt-get install -y --no-install-recommends \
        xfce4 xfce4-terminal mousepad adwaita-icon-theme \
        dbus-x11 x11-xserver-utils xauth \
        tigervnc-standalone-server tigervnc-tools \
        novnc websockify python3-numpy \
        firefox-esr fonts-dejavu-core \
   && apt-get clean && rm -rf /var/lib/apt/lists/* ; \
    fi

# 3. OpenSSH
RUN mkdir -p /run/sshd \
 && sed -i 's/^#\?PermitRootLogin .*/PermitRootLogin yes/' /etc/ssh/sshd_config \
 && sed -i 's/^#\?PasswordAuthentication .*/PasswordAuthentication yes/' /etc/ssh/sshd_config \
 && sed -i 's@session\s*required\s*pam_loginuid.so@session optional pam_loginuid.so@g' /etc/pam.d/sshd

# 4. MOTD + entrypoint
RUN printf '%s\n' \
    '=========================================================' \
    '   Bienvenue sur votre machine InsaCloud (Debian 12)     ' \
    '   Cette machine est louee pour une duree limitee.       ' \
    '   Elle sera detruite automatiquement a expiration.      ' \
    '=========================================================' \
    > /etc/motd

COPY entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh

EXPOSE 22 7681 6080
CMD ["/usr/local/bin/entrypoint.sh"]
