"""
test_faucheur.py - Vérifie le démon de destruction des machines expirées.

Le sujet impose que les machines louées soient détruites (`docker rm -f`) à la
fin de leur durée de location. Ces tests contrôlent ce comportement ainsi que
la réconciliation Docker <-> base de données, sans aucun conteneur réel : les
appels à Docker sont remplacés par une fausse commande pilotée par le test.
"""

import subprocess

import pytest

import database as db
import faucheur
import workers as wk


class FauxDocker:
    """Remplace `subprocess.run` : enregistre les commandes et rejoue des réponses."""

    def __init__(self, reponses=None):
        self.appels = []
        self.reponses = reponses or {}

    def __call__(self, *args, worker="local"):
        self.appels.append((worker, list(args)))
        sortie, code, erreur = self.reponses.get(args[0], ("", 0, ""))
        return subprocess.CompletedProcess(args, returncode=code, stdout=sortie, stderr=erreur)


@pytest.fixture
def docker(monkeypatch):
    faux = FauxDocker()
    monkeypatch.setattr(faucheur, "_docker", faux)
    return faux


def creer_utilisateur(nom="etudiant"):
    return db.create_user(nom, "pbkdf2:sha256:1$x$y")


def louer(user_id, minutes, container_id="abc123456789", nom="insacloud_etudiant_1",
          port=8001, worker="local"):
    return db.create_instance(user_id, container_id, nom, port, minutes,
                              "ubuntu", "terminal", port + 100,
                              gui_port=None, worker=worker)


# --- Destruction des machines expirées --------------------------------------
def test_machine_expiree_detruite_par_docker_rm_f(docker):
    utilisateur = creer_utilisateur()
    identifiant = louer(utilisateur, minutes=-1)          # déjà expirée
    assert faucheur.reap_expired() == 1
    assert docker.appels == [("local", ["rm", "-f", "abc123456789"])], \
        "le sujet impose docker rm -f sur la machine expirée"
    assert db.get_instance(identifiant)["status"] == db.STATUS_EXPIRED


def test_machine_encore_valide_epargnee(docker):
    utilisateur = creer_utilisateur()
    identifiant = louer(utilisateur, minutes=60)
    assert faucheur.reap_expired() == 0
    assert docker.appels == []
    assert db.get_instance(identifiant)["status"] == db.STATUS_RUNNING


def test_echec_docker_conserve_l_instance_pour_le_cycle_suivant(monkeypatch):
    """Si Docker refuse la suppression, l'instance reste 'running' et sera réessayée."""
    monkeypatch.setattr(faucheur, "_docker",
                        FauxDocker({"rm": ("", 1, "daemon not responding")}))
    utilisateur = creer_utilisateur()
    identifiant = louer(utilisateur, minutes=-1)
    faucheur.reap_expired()
    assert db.get_instance(identifiant)["status"] == db.STATUS_RUNNING


def test_conteneur_deja_absent_considere_comme_supprime(monkeypatch):
    monkeypatch.setattr(faucheur, "_docker",
                        FauxDocker({"rm": ("", 1, "Error: No such container: abc")}))
    utilisateur = creer_utilisateur()
    identifiant = louer(utilisateur, minutes=-1)
    faucheur.reap_expired()
    assert db.get_instance(identifiant)["status"] == db.STATUS_EXPIRED


def test_plusieurs_machines_expirees_traitees_en_un_cycle(docker):
    utilisateur = creer_utilisateur()
    for i in range(3):
        louer(utilisateur, minutes=-1, container_id=f"cid{i}23456789",
              nom=f"insacloud_etudiant_{i}", port=8010 + i)
    assert faucheur.reap_expired() == 3
    assert len(docker.appels) == 3


# --- Réconciliation Docker <-> base de données ------------------------------
def test_conteneur_orphelin_supprime(monkeypatch):
    """Un conteneur insacloud_* sans instance active en base est détruit."""
    faux = FauxDocker({"ps": ("deadbeef1234 insacloud_inconnu_1\n", 0, "")})
    monkeypatch.setattr(faucheur, "_docker", faux)
    faucheur.reconcile()
    assert ("local", ["rm", "-f", "deadbeef1234"]) in faux.appels


def test_instance_sans_conteneur_passe_a_stopped(monkeypatch):
    """Une instance 'running' dont le conteneur a disparu est marquée arrêtée."""
    monkeypatch.setattr(faucheur, "_docker", FauxDocker({"ps": ("", 0, "")}))
    utilisateur = creer_utilisateur()
    identifiant = louer(utilisateur, minutes=60)
    faucheur.reconcile()
    assert db.get_instance(identifiant)["status"] == db.STATUS_STOPPED


def test_instance_et_conteneur_coherents_non_modifies(monkeypatch):
    monkeypatch.setattr(faucheur, "_docker",
                        FauxDocker({"ps": ("abc123456789 insacloud_etudiant_1\n", 0, "")}))
    utilisateur = creer_utilisateur()
    identifiant = louer(utilisateur, minutes=60)
    faucheur.reconcile()
    assert db.get_instance(identifiant)["status"] == db.STATUS_RUNNING


def test_noeud_injoignable_ne_detruit_rien(monkeypatch):
    """Exigence de résilience : un worker éteint ne doit pas faire perdre l'état en base."""
    faux = FauxDocker({"ps": ("", 1, "ssh: connect to host 192.168.56.11 port 22: No route to host")})
    monkeypatch.setattr(faucheur, "_docker", faux)
    utilisateur = creer_utilisateur()
    identifiant = louer(utilisateur, minutes=60)
    faucheur.reconcile()
    assert db.get_instance(identifiant)["status"] == db.STATUS_RUNNING
    assert not any(cmd[0] == "rm" for _, cmd in faux.appels)


def test_reconciliation_interroge_chaque_noeud(monkeypatch):
    monkeypatch.setattr(wk, "WORKERS", {"worker1": "192.168.56.11", "worker2": "192.168.56.12"})
    faux = FauxDocker({"ps": ("", 0, "")})
    monkeypatch.setattr(faucheur, "_docker", faux)
    faucheur.reconcile()
    assert {worker for worker, _ in faux.appels} == {"worker1", "worker2"}


# --- Robustesse du démon -----------------------------------------------------
def test_docker_absent_ne_fait_pas_planter_le_demon(monkeypatch):
    def explose(*args, **kwargs):
        raise FileNotFoundError("docker")

    monkeypatch.setattr(subprocess, "run", explose)
    resultat = faucheur._docker("ps", worker="local")
    assert resultat.returncode == 1, "l'erreur est convertie en code retour, pas en exception"


def test_docker_qui_ne_repond_pas_est_traite_en_erreur(monkeypatch):
    def trop_lent(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd="docker", timeout=60)

    monkeypatch.setattr(subprocess, "run", trop_lent)
    assert faucheur._docker("ps", worker="local").returncode == 1


def test_signal_d_arret_stoppe_la_boucle():
    faucheur._stop_event.clear()
    faucheur.stop()
    assert faucheur._stop_event.is_set()
    faucheur._stop_event.clear()
