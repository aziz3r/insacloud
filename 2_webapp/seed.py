#!/usr/bin/env python3
"""
seed.py - Jeu de données de démonstration.

Crée des comptes, des nœuds et des locations dans des états variés, pour
pouvoir montrer l'interface sans attendre qu'une location expire d'elle-même.

    python3 seed.py                 remplit la base configurée
    python3 seed.py --reset         vide d'abord les tables métier
    python3 seed.py --mots-de-passe affiche les identifiants créés

Ces comptes sont réservés à la démonstration et au développement : le script
refuse de s'exécuter si INSACLOUD_ENV vaut « production ».
"""

import argparse
import os
import sys
from datetime import timedelta

from werkzeug.security import generate_password_hash

import database as db
from models import (INSTANCE_EXPIRED, INSTANCE_RUNNING, RENTAL_ACTIVE,
                    RENTAL_EXPIRED, WORKER_AVAILABLE, WORKER_OFFLINE, Instance,
                    Rental, User, Worker, utc_now)

# Mot de passe commun aux comptes de démonstration. Il n'ouvre rien d'autre
# que cette base jetable, et le script refuse de tourner en production.
MOT_DE_PASSE_DEMO = "Demonstration-2026!"

COMPTES = [
    ("alice", "alice@insa-cvl.fr"),
    ("bob", "bob@insa-cvl.fr"),
    ("charlie", "charlie@insa-cvl.fr"),
]

NOEUDS = [
    # nom, adresse, cœurs, mémoire (Mio), état
    ("worker1", "192.168.56.11", 2, 3072, WORKER_AVAILABLE),
    ("worker2", "192.168.56.12", 2, 3072, WORKER_AVAILABLE),
    ("worker3", "192.168.56.13", 4, 4096, WORKER_OFFLINE),   # panne simulée
]


def refuser_en_production() -> None:
    if os.environ.get("INSACLOUD_ENV", "development").lower() == "production":
        print("seed.py refuse de s'exécuter en production.", file=sys.stderr)
        sys.exit(2)


def vider() -> None:
    """Efface les données métier, en respectant l'ordre des clés étrangères."""
    with db.SessionLocal() as session:
        for modele in (Rental, Instance, Worker, User):
            session.query(modele).delete()
        session.commit()
    print("  tables métier vidées")


def remplir() -> None:
    db.init_db()
    maintenant = utc_now()

    with db.SessionLocal() as session:
        utilisateurs = {}
        for nom, courriel in COMPTES:
            if session.query(User).filter_by(username=nom).first():
                continue
            utilisateur = User(username=nom, email=courriel,
                               password_hash=generate_password_hash(MOT_DE_PASSE_DEMO))
            session.add(utilisateur)
            utilisateurs[nom] = utilisateur
        session.commit()

        noeuds = {}
        for nom, ip, cpu, memoire, etat in NOEUDS:
            worker = session.query(Worker).filter_by(hostname=nom).first()
            if worker is None:
                worker = Worker(hostname=nom, ip=ip)
                session.add(worker)
            worker.cpu, worker.memory, worker.status = cpu, memoire, etat
            worker.capacity = max(1, (memoire - 1024) // 256)
            # Le nœud en panne n'a plus donné signe de vie depuis dix minutes.
            worker.last_heartbeat = (maintenant - timedelta(minutes=10)
                                     if etat == WORKER_OFFLINE else maintenant)
            noeuds[nom] = worker
        session.commit()

        alpine = session.query(db.Distribution).filter_by(name="alpine").first()
        ubuntu = session.query(db.Distribution).filter_by(name="ubuntu").first()

        # Trois locations dans trois états : en cours, expirée, longue durée.
        locations = [
            ("alice", "worker1", alpine, "terminal", 8101, 30, RENTAL_ACTIVE, INSTANCE_RUNNING),
            ("bob", "worker2", ubuntu, "desktop", 8102, -5, RENTAL_EXPIRED, INSTANCE_EXPIRED),
            ("charlie", "worker1", alpine, "terminal", 8103, 90, RENTAL_ACTIVE, INSTANCE_RUNNING),
        ]
        for nom, noeud, distro, mode, port, minutes, etat_loc, etat_inst in locations:
            utilisateur = session.query(User).filter_by(username=nom).first()
            if session.query(Rental).filter_by(user_id=utilisateur.id).first():
                continue
            instance = Instance(
                container_id=f"demo{port}00000000", container_name=f"insacloud_{nom}_demo",
                worker_id=noeuds[noeud].id, distribution_id=distro.id,
                ssh_port=port, mode=mode, term_port=port + 100,
                gui_port=port + 200 if mode == "desktop" else None,
                status=etat_inst,
                terminated_at=maintenant if etat_inst != INSTANCE_RUNNING else None)
            session.add(instance)
            session.flush()
            session.add(Rental(user_id=utilisateur.id, instance_id=instance.id,
                               start_time=maintenant - timedelta(minutes=10),
                               end_time=maintenant + timedelta(minutes=minutes),
                               status=etat_loc))
        session.commit()

    statistiques = db.get_statistics()
    print(f"  {statistiques['users']} comptes, {statistiques['workers_total']} nœuds "
          f"({statistiques['workers_offline']} hors ligne), "
          f"{statistiques['instances_total']} machines dont "
          f"{statistiques['instances_running']} actives")


def main() -> int:
    analyseur = argparse.ArgumentParser(description=__doc__)
    analyseur.add_argument("--reset", action="store_true",
                           help="vider les tables métier avant de remplir")
    analyseur.add_argument("--mots-de-passe", action="store_true",
                           help="afficher les identifiants créés")
    arguments = analyseur.parse_args()

    refuser_en_production()
    print(f"Base : {db.url_sans_secret()}")
    if arguments.reset:
        vider()
    remplir()
    if arguments.mots_de_passe:
        print("\n  Comptes de démonstration (mot de passe commun) :")
        for nom, courriel in COMPTES:
            print(f"    {nom:9} {courriel:24} {MOT_DE_PASSE_DEMO}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
