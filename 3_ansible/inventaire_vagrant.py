#!/usr/bin/env python3
"""
inventaire_vagrant.py - Inventaire Ansible dynamique pour les VM Vagrant.

Pourquoi un inventaire dynamique ?
    L'inventaire statique (inventaire.ini) fige les adresses IP et le chemin de
    la clé privée, qui dépend du provider : .vagrant/machines/<vm>/virtualbox/
    sur PC, mais .../vmware_desktop/ ou .../parallels/ sur Mac Apple Silicon.
    Ce script interroge Vagrant lui-même (`vagrant ssh-config`), qui connaît la
    vérité : le même dépôt fonctionne donc sur les quatre providers, sans
    qu'aucune adresse ne soit écrite à la main.

Utilisation
    ansible-playbook -i inventaire_vagrant.py site.yml --ask-vault-pass
    ansible -i inventaire_vagrant.py -m ping insacloud
    ./inventaire_vagrant.py --list        (affiche l'inventaire au format JSON)

Groupes produits
    controllers  la machine dont le nom commence par « controller »
    workers      les machines dont le nom commence par « worker »
    insacloud    les deux précédents (comme [insacloud:children] du .ini)
"""

import argparse
import json
import os
import shutil
import subprocess  # nosec B404 - Vagrant ne s'interroge que par sa ligne de commande
import sys

# Répertoire du projet : ce script vit dans 3_ansible/, le Vagrantfile un cran au-dessus.
RACINE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

VARIABLES_COMMUNES = {
    "ansible_become": True,
    "ansible_become_method": "sudo",
    "ansible_python_interpreter": "/usr/bin/python3",
}


def lire_ssh_config() -> str:
    """Retourne la sortie de `vagrant ssh-config`, ou lève une erreur explicite."""
    # Chemin absolu résolu une fois : on n'exécute pas un simple nom qu'un
    # répertoire du PATH pourrait détourner.
    vagrant = shutil.which("vagrant")
    if vagrant is None:
        raise RuntimeError(
            "Vagrant est introuvable. Utilisez l'inventaire statique :\n"
            "  ansible-playbook -i inventaire.ini site.yml"
        )
    resultat = subprocess.run(  # nosec B603  # noqa: S603
        [vagrant, "ssh-config"], cwd=RACINE,
        capture_output=True, text=True, timeout=120, check=False,
    )
    if resultat.returncode != 0:
        raise RuntimeError(
            "`vagrant ssh-config` a échoué. Les machines sont-elles démarrées "
            "(`vagrant up`) ?\n" + resultat.stderr.strip()
        )
    return resultat.stdout


def analyser(sortie: str) -> dict:
    """Transforme la sortie OpenSSH de Vagrant en {nom_de_machine: {options}}."""
    machines, courante = {}, None
    for ligne in sortie.splitlines():
        ligne = ligne.strip()
        if not ligne or ligne.startswith("#"):
            continue
        cle, _, valeur = ligne.partition(" ")
        valeur = valeur.strip()
        if cle == "Host":
            courante = valeur
            machines[courante] = {}
        elif courante:
            machines[courante][cle] = valeur
    return machines


def construire(machines: dict) -> dict:
    """Assemble l'inventaire attendu par Ansible à partir des machines Vagrant."""
    inventaire = {
        "_meta": {"hostvars": {}},
        "controllers": {"hosts": []},
        "workers": {"hosts": []},
        "insacloud": {"children": ["controllers", "workers"], "vars": VARIABLES_COMMUNES},
    }
    for nom, options in sorted(machines.items()):
        groupe = "controllers" if nom.startswith("controller") else \
                 "workers" if nom.startswith("worker") else None
        if groupe is None:
            continue                       # machine hors périmètre InsaCloud
        inventaire[groupe]["hosts"].append(nom)
        inventaire["_meta"]["hostvars"][nom] = {
            "ansible_host": options.get("HostName", nom),
            "ansible_port": int(options.get("Port", 22)),
            "ansible_user": options.get("User", "vagrant"),
            # Chemin découvert par Vagrant : il désigne le bon provider sans
            # que celui-ci ait à être connu de l'inventaire.
            "ansible_ssh_private_key_file": options.get("IdentityFile", "").strip('"'),
            "ansible_ssh_common_args": "-o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null",
            # Adresse du réseau privé, utilisée par les rôles pour que les nœuds
            # se parlent entre eux (le certificat TLS et docker -H ssh:// s'en servent).
            "node_ip": adresse_privee(nom),
        }
    return inventaire


def adresse_privee(nom: str) -> str:
    """
    Adresse du réseau privé déclarée dans le Vagrantfile (192.168.56.10/.11/.12).
    Surchargeable par INSACLOUD_SUBNET pour un autre plan d'adressage.
    """
    base = os.environ.get("INSACLOUD_SUBNET", "192.168.56")
    derniers = {"controller": 10, "worker1": 11, "worker2": 12}
    return f"{base}.{derniers.get(nom, 10)}"


def main() -> int:
    analyseur = argparse.ArgumentParser(description=__doc__)
    analyseur.add_argument("--list", action="store_true", help="inventaire complet (défaut)")
    analyseur.add_argument("--host", metavar="MACHINE", help="variables d'une seule machine")
    arguments = analyseur.parse_args()

    try:
        machines = analyser(lire_ssh_config())
    except RuntimeError as erreur:
        print(str(erreur), file=sys.stderr)
        return 1

    inventaire = construire(machines)
    if arguments.host:
        print(json.dumps(inventaire["_meta"]["hostvars"].get(arguments.host, {}), indent=2))
    else:
        print(json.dumps(inventaire, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
