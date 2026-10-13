"""
test_contraintes.py - Ce que la BASE refuse, indépendamment du code applicatif.

L'énoncé demande de « tester les contraintes d'intégrité ». La distinction
compte : une règle tenue par le moteur reste vraie même si une requête
concurrente, un script d'administration ou un futur appel oublient de la
vérifier. Une règle tenue seulement par le code applicatif ne tient que tant
que personne ne contourne ce code.

Trois familles sont vérifiées ici :
  * l'unicité du port sur un nœud, limitée aux machines actives (index partiel) ;
  * l'unicité de l'identifiant et de l'adresse d'un compte ;
  * les clés étrangères, qui interdisent une location sans utilisateur.
"""

import os

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError

import database as db
from models import Instance, Rental, User


# =============================================================================
#  L'index unique partiel sur (worker_id, ssh_port)
# =============================================================================
def test_deux_machines_actives_ne_partagent_pas_un_port():
    """
    C'est la contrainte qui empêche deux conteneurs de se disputer un port.
    Le code applicatif choisit un port libre, mais entre « ce port est libre »
    et « je le prends » il s'écoule un instant : seul le moteur peut trancher.
    """
    utilisateur = db.create_user("etudiant", "pbkdf2:sha256:1$x$y")
    db.create_instance(utilisateur, "cid0001", "insacloud_a", 8001, 30,
                       "ubuntu", "terminal", 8101)

    with pytest.raises(IntegrityError):
        with db.SessionLocal() as session:
            session.add(Instance(
                container_id="cid0002", container_name="insacloud_b",
                worker_id=1, distribution_id=1, ssh_port=8001,   # le MÊME port
                status=db.STATUS_RUNNING, mode="terminal"))
            session.commit()


def test_un_port_redevient_libre_quand_la_machine_est_detruite():
    """
    L'index est PARTIEL : il ne vaut que pour les machines en cours. Sans la
    clause « where », un port serait réservé pour toujours par une machine
    détruite, et la plage 8000-9000 s'épuiserait.
    """
    utilisateur = db.create_user("etudiant", "pbkdf2:sha256:1$x$y")
    identifiant = db.create_instance(utilisateur, "cid0001", "insacloud_a", 8001, 30,
                                     "ubuntu", "terminal", 8101)
    db.set_instance_status(identifiant, db.STATUS_EXPIRED)

    # Le même port, sur le même nœud : accepté, puisque la première n'est plus active.
    second = db.create_instance(utilisateur, "cid0002", "insacloud_b", 8001, 30,
                                "ubuntu", "terminal", 8102)
    assert db.get_instance(second)["port"] == 8001


# =============================================================================
#  L'unicité des comptes
# =============================================================================
def test_deux_comptes_ne_partagent_pas_un_identifiant():
    db.create_user("etudiant", "pbkdf2:sha256:1$x$y")

    with pytest.raises(IntegrityError):
        with db.SessionLocal() as session:
            session.add(User(username="etudiant", password_hash="autre"))
            session.commit()


def test_deux_comptes_ne_partagent_pas_une_adresse():
    db.create_user("alice", "pbkdf2:sha256:1$x$y", email="a@insa-cvl.fr")

    with pytest.raises(IntegrityError):
        with db.SessionLocal() as session:
            session.add(User(username="bob", password_hash="x", email="a@insa-cvl.fr"))
            session.commit()


# =============================================================================
#  Les clés étrangères
# =============================================================================
def test_une_location_sans_utilisateur_est_refusee():
    """
    Sans cette contrainte, une suppression de compte laisserait des locations
    orphelines — et le tableau de bord planterait en cherchant leur propriétaire.
    """
    utilisateur = db.create_user("etudiant", "pbkdf2:sha256:1$x$y")
    identifiant = db.create_instance(utilisateur, "cid0001", "insacloud_a", 8001, 30,
                                     "ubuntu", "terminal", 8101)

    with db.SessionLocal() as session:
        # SQLite n'applique les clés étrangères que si on le lui demande ;
        # PostgreSQL le fait toujours. On force, pour tester la même règle.
        session.execute(text("PRAGMA foreign_keys = ON"))
        with pytest.raises(IntegrityError):
            session.add(Rental(user_id=999999, instance_id=identifiant,
                               end_time=db.utc_now(), status=db.RENTAL_ACTIVE))
            session.commit()


def test_une_instance_sans_noeud_est_refusee():
    with db.SessionLocal() as session:
        session.execute(text("PRAGMA foreign_keys = ON"))
        with pytest.raises(IntegrityError):
            session.add(Instance(
                container_id="cid9999", container_name="insacloud_x",
                worker_id=999999, distribution_id=1, ssh_port=8999,
                status=db.STATUS_RUNNING, mode="terminal"))
            session.commit()


# =============================================================================
#  Ce que le catalogue garantit
# =============================================================================
def test_le_catalogue_ne_contient_pas_deux_fois_la_meme_distribution():
    """init_db() est appelé par chaque processus Gunicorn au démarrage : il doit
    pouvoir l'être plusieurs fois sans dupliquer le catalogue."""
    db.init_db()
    db.init_db()

    with db.SessionLocal() as session:
        noms = [n for (n,) in session.execute(select(db.Distribution.name)).all()]
    assert len(noms) == len(set(noms)), "le catalogue a été dupliqué"


# --- Le pool de connexions et le fork ---------------------------------------
@pytest.mark.skipif(not hasattr(os, "fork"), reason="pas de fork sur cette plateforme")
def test_un_fils_abandonne_le_pool_herite():
    """
    gunicorn tourne avec « --preload » : il importe l'application AVANT de
    forker ses quatre workers, et app.py appelle init_db() à l'import. La
    connexion ouverte à ce moment se range dans le pool du processus maître et
    serait héritée par les fils, qui se serviraient de la MÊME socket
    PostgreSQL en même temps.

    Le défaut ne se voyait qu'au démarrage — trois des huit premières requêtes
    en erreur, puis plus rien — et l'erreur remontée (« connection in
    transaction status INTRANS ») n'avait aucun rapport avec la requête. Ce
    test vérifie que chaque fils repart d'un pool neuf.
    """
    with db.engine.connect() as connexion:
        connexion.execute(text("SELECT 1"))     # le pool contient une connexion

    pool_du_parent = id(db.engine.pool)
    lecture, ecriture = os.pipe()
    pid = os.fork()
    if pid == 0:                                 # --- le fils ---
        try:
            different = id(db.engine.pool) != pool_du_parent
            os.write(ecriture, b"1" if different else b"0")
        finally:
            os._exit(0)                          # jamais sys.exit : pas de atexit ici

    os.close(ecriture)
    os.waitpid(pid, 0)
    reponse = os.read(lecture, 1)
    os.close(lecture)
    assert reponse == b"1", "le fils a hérité du pool du parent"
