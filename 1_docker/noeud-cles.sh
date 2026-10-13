#!/bin/sh
# =============================================================================
#  Fabrique la clé de pilotage du contrôleur, une seule fois, dans le volume
#  partagé. Le contrôleur s'en sert pour joindre les nœuds ; les nœuds en
#  installent la partie publique dans authorized_keys.
#
#  La clé appartient à l'uid de l'application (10001) en 0600 : OpenSSH refuse
#  d'utiliser une clé privée lisible par d'autres, ou dont le propriétaire
#  n'est ni root ni l'utilisateur courant.
# =============================================================================
set -eu

CLES=${INSACLOUD_CLES:-/cles}
UID_APP=${INSACLOUD_UID_APP:-10001}

if [ ! -f "$CLES/id_ed25519" ]; then
    ssh-keygen -t ed25519 -N '' -C "insacloud-controleur" -f "$CLES/id_ed25519" >/dev/null
    echo "Clé de pilotage créée dans $CLES."
else
    echo "Clé de pilotage déjà présente, conservée."
fi

# Le client Docker lance « ssh » sans options : tout passe par ce fichier.
cat > "$CLES/config" <<'CONFIG'
Host *
    User insacloud
    IdentityFile ~/.ssh/id_ed25519
    IdentitiesOnly yes
    ConnectTimeout 5
    ServerAliveInterval 5
    ServerAliveCountMax 2
    LogLevel ERROR
    # Les nœuds sont recréés à chaque démarrage de la pile : leur empreinte
    # change, et il n'y a pas d'autorité pour la certifier. Sur un vrai parc,
    # Ansible dépose les empreintes et cette tolérance disparaît.
    StrictHostKeyChecking no
    UserKnownHostsFile /dev/null
CONFIG

chown -R "$UID_APP:$UID_APP" "$CLES"
chmod 700 "$CLES"
chmod 600 "$CLES/id_ed25519" "$CLES/config"
chmod 644 "$CLES/id_ed25519.pub"
