"""
test_archivage_destruction.py - L'archivage se déclenche juste avant la destruction.

Trois garanties à tenir :
  1. l'archive est faite AVANT `docker rm -f` (après, il n'y a plus rien à sauvegarder) ;
  2. un archivage raté ne bloque JAMAIS la destruction de la machine ;
  3. une machine n'est archivée qu'une fois, même si sa destruction est retentée.
"""

import subprocess

import pytest

import database as db
import faucheur
import services
from models import WORKER_OFFLINE


@pytest.fixture
def journal():
    """Liste ordonnée des événements : « archive », « rm »."""
    return []


@pytest.fixture
def espion(monkeypatch, journal):
    """Active l'archivage et remplace l'archivage réel par un faux qui note ses appels."""
    class Espion:
        appels = []
        resultat = {"status": "ok", "path": "1/1-aaaa.tar.gz", "size": 10, "sha256": "ab" * 32}
        panne = None

    def faux(container_id, worker, user_id, instance_id):
        journal.append("archive")
        Espion.appels.append((container_id, worker, user_id, instance_id))
        if Espion.panne:
            raise Espion.panne
        return Espion.resultat

    Espion.appels = []
    monkeypatch.setattr(services, "ARCHIVE_ENABLED", True)
    monkeypatch.setattr(services.archives, "archiver_conteneur", faux)
    return Espion


@pytest.fixture
def docker(monkeypatch, journal):
    """Fausse commande Docker : `rm -f` réussit et se note dans le journal."""
    class Faux:
        code = 0

    def faux(*args, worker="local"):
        if args[0] == "rm":
            journal.append("rm")
        return subprocess.CompletedProcess(args, returncode=Faux.code, stdout="", stderr="")

    monkeypatch.setattr(faucheur, "_docker", faux)
    return Faux


def louer(minutes=-1, worker="local", port=8001):
    utilisateur = db.create_user(f"etudiant{port}", "pbkdf2:sha256:1$x$y")
    identifiant = db.create_instance(utilisateur, f"cid{port}abcdef", f"insacloud_m_{port}", port, minutes,
                                     "ubuntu", "terminal", port + 100, gui_port=None, worker=worker)
    return utilisateur, identifiant


def test_desactive_par_defaut_aucun_archivage(monkeypatch, docker, journal):
    appels = []
    monkeypatch.setattr(services.archives, "archiver_conteneur", lambda *a: appels.append(a))
    _, identifiant = louer()

    faucheur.reap_expired()

    assert appels == [], "l'archivage est désactivé tant qu'on ne l'active pas"
    assert db.get_instance(identifiant)["status"] == db.STATUS_EXPIRED


def test_expiration_archive_puis_detruit(espion, docker, journal):
    utilisateur, identifiant = louer()

    faucheur.reap_expired()

    assert journal == ["archive", "rm"], "l'archive doit précéder la destruction"
    assert espion.appels == [("cid8001abcdef", "local", utilisateur, identifiant)]
    liste = db.get_user_archives(utilisateur)
    assert [a["status"] for a in liste] == ["ok"]
    assert db.get_instance(identifiant)["status"] == db.STATUS_EXPIRED


def test_archivage_rate_ne_bloque_pas_la_destruction(espion, docker, journal):
    espion.resultat = {"status": "failed", "detail": "ssh injoignable"}
    utilisateur, identifiant = louer()

    faucheur.reap_expired()

    assert db.get_instance(identifiant)["status"] == db.STATUS_EXPIRED
    assert [a["status"] for a in db.get_user_archives(utilisateur)] == ["failed"]


def test_erreur_inattendue_ne_bloque_pas_la_destruction(espion, docker, journal):
    espion.panne = RuntimeError("bug imprévu")
    _, identifiant = louer()

    faucheur.reap_expired()

    assert journal == ["archive", "rm"]
    assert db.get_instance(identifiant)["status"] == db.STATUS_EXPIRED


def test_destruction_retentee_n_archive_qu_une_fois(espion, docker, journal):
    docker.code = 1                                  # docker rm échoue au premier passage
    utilisateur, identifiant = louer()

    faucheur.reap_expired()
    assert db.get_instance(identifiant)["status"] == db.STATUS_RUNNING

    docker.code = 0                                  # il réussit au passage suivant
    faucheur.reap_expired()

    assert len(espion.appels) == 1, "pas une archive par cycle du Faucheur"
    assert len(db.get_user_archives(utilisateur)) == 1
    assert db.get_instance(identifiant)["status"] == db.STATUS_EXPIRED


def test_noeud_hors_ligne_pas_d_attente_inutile(espion, docker, journal):
    db.ensure_worker("worker1", "10.0.0.2")
    db.set_worker_status("worker1", WORKER_OFFLINE)
    utilisateur, identifiant = louer(worker="worker1")

    faucheur.reap_expired()

    assert espion.appels == [], "on n'attend pas le délai maximal sur un nœud qu'on sait mort"
    assert [a["status"] for a in db.get_user_archives(utilisateur)] == ["failed"]
    assert db.get_instance(identifiant)["status"] == db.STATUS_EXPIRED


def test_arret_manuel_archive_avant_de_detruire(espion, monkeypatch, journal):
    monkeypatch.setattr(services.docker, "docker_remove_container",
                        lambda container_id, worker: journal.append("rm"))
    utilisateur, identifiant = louer(minutes=30)

    resultat = services.arreter(identifiant, utilisateur)

    assert resultat.ok
    assert journal == ["archive", "rm"]
    assert [a["status"] for a in db.get_user_archives(utilisateur)] == ["ok"]
    assert db.get_instance(identifiant)["status"] == db.STATUS_STOPPED
