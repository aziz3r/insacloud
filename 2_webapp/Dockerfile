# =============================================================================
#  2_webapp/Dockerfile - Image de l'application InsaCloud (Flask + Gunicorn)
# -----------------------------------------------------------------------------
#  À ne pas confondre avec les Dockerfile de 1_docker/ : ceux-là produisent les
#  machines LOUÉES, celui-ci produit la plateforme qui les loue.
#
#  Construction en deux étapes : les dépendances sont installées dans un étage
#  jetable, seul le résultat est copié dans l'image finale. Les outils de
#  compilation ne se retrouvent donc pas dans l'image publiée.
#
#  L'application tourne sous un compte sans privilège (insacloud, uid 10001).
# =============================================================================

# --- Étage 0 : client Docker officiel ----------------------------------------
# Seul le CLIENT est nécessaire : l'application pilote un démon distant
# (docker -H ssh://worker) ou la socket de l'hôte. Le prendre dans l'image
# officielle évite de dépendre du découpage des paquets Debian, où le client
# a changé de paquet, et donne un binaire unique de quelques mégaoctets.
FROM docker:28-cli AS dockercli

# --- Étage 1 : dépendances ---------------------------------------------------
FROM python:3.12-slim AS builder

ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /build
COPY requirements.txt .
# --prefix : l'arborescence installée est copiable telle quelle dans l'étage final
RUN python -m pip install --prefix=/install -r requirements.txt

# --- Étage 2 : image finale --------------------------------------------------
FROM python:3.12-slim

LABEL org.opencontainers.image.title="InsaCloud" \
      org.opencontainers.image.description="Plateforme de location de machines Linux" \
      org.opencontainers.image.source="https://github.com/aziz3r/insacloud" \
      org.opencontainers.image.licenses="MIT"

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    INSACLOUD_ENV=production \
    INSACLOUD_HOST=0.0.0.0 \
    INSACLOUD_PORT=5000

# curl pour la sonde de santé, openssh-client pour le transport `docker -H ssh://`
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl openssh-client \
    && rm -rf /var/lib/apt/lists/*

COPY --from=dockercli /usr/local/bin/docker /usr/local/bin/docker
COPY --from=builder /install /usr/local

# Compte sans privilège : rien dans cette image ne tourne en root.
RUN groupadd --gid 10001 insacloud \
    && useradd --uid 10001 --gid 10001 --create-home --shell /usr/sbin/nologin insacloud

WORKDIR /app
COPY --chown=insacloud:insacloud . /app
# Les clés et la base sont fournies par l'environnement ou un volume : rien
# de secret ne doit être figé dans une couche de l'image.
RUN rm -f /app/.secret_key /app/.vault_key /app/*.db /app/*.db-wal /app/*.db-shm \
    && rm -rf /app/.venv /app/tests /app/__pycache__

# Identifiant numérique : résoluble quel que soit le /etc/passwd de l'hôte,
# ce qui compte pour les contrôles de sécurité des orchestrateurs.
USER 10001:10001
EXPOSE 5000

# La sonde interroge /health, qui vérifie aussi l'accès à la base.
# curl renvoie déjà un code non nul en cas d'échec : pas de « || exit 1 ».
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD ["curl", "-fsS", "http://127.0.0.1:5000/health"]

# --preload : le schéma est créé une seule fois, avant le fork des workers.
CMD ["gunicorn", "--workers", "4", "--bind", "0.0.0.0:5000", \
     "--timeout", "60", "--preload", \
     "--access-logfile", "-", "--error-logfile", "-", "app:app"]
