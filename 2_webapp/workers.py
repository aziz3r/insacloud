# -*- coding: utf-8 -*-
"""
workers.py - Nœuds Docker (« workers ») qui hébergent les machines louées.

Deux modes, sans changer une ligne ailleurs :
  - INSACLOUD_WORKERS vide  -> mode local : le Docker de la machine courante
    (développement, démo sur un seul hôte) ; le nœud s'appelle "local".
  - INSACLOUD_WORKERS="worker1=192.168.56.11,worker2=192.168.56.12"
    -> chaque commande docker est exécutée sur le nœud choisi via le transport
    SSH du client Docker (`docker -H ssh://insacloud@IP …`). Rien n'est exposé
    sur le réseau à part SSH ; l'authentification se fait par clé.

Répartition : le nouveau conteneur va sur le nœud qui héberge le moins de
machines actives (équilibrage simple et prévisible).
"""

import os

DOCKER_SSH_USER = os.environ.get("INSACLOUD_DOCKER_SSH_USER", "insacloud")


def load_workers() -> dict:
    """Retourne {nom: hôte_ou_None} dans l'ordre de configuration."""
    raw = os.environ.get("INSACLOUD_WORKERS", "").strip()
    if not raw:
        return {"local": None}
    workers = {}
    for item in raw.split(","):
        item = item.strip()
        if not item:
            continue
        name, _, host = item.partition("=")
        name, host = name.strip(), host.strip()
        if name and host:
            workers[name] = host
    return workers or {"local": None}


WORKERS = load_workers()


def worker_host(worker: str):
    """Adresse du nœud (None = Docker local)."""
    return WORKERS.get(worker)


def docker_command(worker: str = "local") -> list:
    """Préfixe de commande docker pour ce nœud."""
    host = worker_host(worker)
    if host is None:
        return ["docker"]
    return ["docker", "-H", f"ssh://{DOCKER_SSH_USER}@{host}"]


def pick_worker(running_per_worker: dict) -> str:
    """Nœud le moins chargé (à égalité : le premier configuré)."""
    return min(WORKERS, key=lambda name: (running_per_worker.get(name, 0), list(WORKERS).index(name)))
