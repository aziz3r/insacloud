"""
test_workers.py - Parc de nœuds, ordonnanceur et reprise sur panne.

Couvre les fonctionnalités 7 (agent et enregistrement dynamique), 8
(gestionnaire de ressources) et 13 (haute disponibilité) du cahier des charges,
sans lancer le moindre conteneur : Docker est remplacé par une fonction espion.
"""

import time

import pytest

import database as db
import docker_ops
import faucheur
import services
from conftest import inscrire
from models import WORKER_AVAILABLE, WORKER_BUSY, WORKER_OFFLINE


@pytest.fixture
def docker_espion(monkeypatch):
    """Remplace l'exécution Docker : on observe les commandes, rien ne tourne."""
    appels = []

    def faux_run(*args, worker="local", timeout=None):
        appels.append({"worker": worker, "commande": args[0], "args": list(args)})
        if args[0] == "images":
            # La plateforme demande quelles images existent avant de proposer
            # une distribution : on répond que les six sont construites.
            return "\n".join(f"insacloud_{d}{s}" for d in ("ubuntu", "debian", "alpine")
                              for s in ("", "_desktop"))
        return "nouveauconteneur0123"

    monkeypatch.setattr(docker_ops, "run_docker", faux_run)
    monkeypatch.setattr(docker_ops, "port_is_free_on_host", lambda port: True)
    # Le cache des images est partagé entre les tests : on le vide pour que
    # chacun reparte de la réponse de SON espion.
    docker_ops._CACHE_IMAGES.update(instant=0.0, noms=frozenset())
    return appels


def deux_workers():
    db.register_worker("worker1", "192.168.56.11", cpu=2, memory=3072)
    db.register_worker("worker2", "192.168.56.12", cpu=2, memory=3072)


# --- Fonctionnalité 7 : enregistrement dynamique ----------------------------
def test_un_worker_s_enregistre():
    worker = db.register_worker("worker1", "192.168.56.11", cpu=4, memory=8192)
    assert worker["status"] == WORKER_AVAILABLE
    assert worker["cpu"] == 4 and worker["memory"] == 8192
    assert worker["last_heartbeat"] is not None


def test_capacite_deduite_de_la_memoire():
    """Une machine « terminal » consomme 256 Mio ; on réserve 1 Gio au système."""
    worker = db.register_worker("worker1", "192.168.56.11", cpu=2, memory=3072)
    assert worker["capacity"] == (3072 - 1024) // 256


def test_un_worker_qui_revient_est_mis_a_jour():
    db.register_worker("worker1", "192.168.56.11", cpu=2, memory=3072)
    db.set_worker_status("worker1", WORKER_OFFLINE)
    revenu = db.register_worker("worker1", "192.168.56.99", cpu=8, memory=16384)
    assert revenu["status"] == WORKER_AVAILABLE, "un nœud qui revient redevient disponible"
    assert revenu["ip"] == "192.168.56.99"
    assert len(db.get_workers()) == 1, "il ne doit pas y avoir de doublon"


def test_battement_de_coeur_d_un_inconnu_refuse():
    assert db.worker_heartbeat("inconnu") is False


def test_battement_de_coeur_remet_en_ligne():
    db.register_worker("worker1", "192.168.56.11")
    db.set_worker_status("worker1", WORKER_OFFLINE)
    assert db.worker_heartbeat("worker1") is True
    assert db.get_worker_by_hostname("worker1")["status"] == WORKER_AVAILABLE


def test_un_worker_peut_se_declarer_occupe():
    db.register_worker("worker1", "192.168.56.11")
    db.worker_heartbeat("worker1", status=WORKER_BUSY)
    assert db.get_worker_by_hostname("worker1")["status"] == WORKER_BUSY


def test_silence_prolonge_fait_passer_hors_ligne(monkeypatch):
    monkeypatch.setattr(db, "HEARTBEAT_TIMEOUT", 1)
    db.register_worker("worker1", "192.168.56.11")
    time.sleep(1.2)
    assert db.mark_stale_workers_offline(1) == ["worker1"]
    assert db.get_worker_by_hostname("worker1")["status"] == WORKER_OFFLINE


# --- Fonctionnalité 8 : ordonnanceur ----------------------------------------
def test_le_moins_charge_est_choisi(docker_espion):
    deux_workers()
    utilisateur = db.create_user("etudiant", "hash")
    db.create_instance(utilisateur, "aaa111222333", "insacloud_etudiant_1", 8001, 30,
                       os_type="alpine", term_port=8101, worker="worker1")
    assert db.select_available_worker()["hostname"] == "worker2"


def test_un_worker_hors_ligne_n_est_jamais_choisi():
    deux_workers()
    db.set_worker_status("worker2", WORKER_OFFLINE)
    assert db.select_available_worker()["hostname"] == "worker1"


def test_un_worker_plein_n_est_plus_choisi():
    db.register_worker("worker1", "192.168.56.11", capacity=1)
    utilisateur = db.create_user("etudiant", "hash")
    db.create_instance(utilisateur, "aaa111222333", "insacloud_etudiant_1", 8001, 30,
                       os_type="alpine", term_port=8101, worker="worker1")
    assert db.select_available_worker() is None, "sa capacité est atteinte"


def test_aucun_worker_disponible():
    deux_workers()
    db.set_worker_status("worker1", WORKER_OFFLINE)
    db.set_worker_status("worker2", WORKER_OFFLINE)
    assert db.select_available_worker() is None


# --- Fonctionnalité 13 : reprise sur panne ----------------------------------
def test_une_machine_est_recreee_sur_un_autre_worker(docker_espion, monkeypatch):
    """Le scénario du cahier des charges : worker1 tombe, l'instance passe sur worker2."""
    monkeypatch.setattr(db, "HEARTBEAT_TIMEOUT", 1)
    deux_workers()
    utilisateur = db.create_user("etudiant", "hash")
    identifiant = db.create_instance(utilisateur, "abc123456789", "insacloud_etudiant_a1",
                                     8001, 30, os_type="alpine", term_port=8101,
                                     worker="worker1")
    avant = db.get_instance(identifiant)

    time.sleep(1.2)
    db.worker_heartbeat("worker2")          # worker2 reste vivant
    assert faucheur.surveiller_workers() == 1

    apres = db.get_instance(identifiant)
    assert apres["worker"] == "worker2", "la machine doit avoir changé de nœud"
    assert apres["status"] == db.STATUS_RUNNING
    assert apres["container_id"] != avant["container_id"], "le conteneur est bien recréé"
    assert apres["migrations"] == 1
    assert db.get_worker_by_hostname("worker1")["status"] == WORKER_OFFLINE
    assert any(a["worker"] == "worker2" and a["commande"] == "run" for a in docker_espion)


def test_la_location_survit_a_la_reprise(docker_espion, monkeypatch):
    """L'utilisateur ne doit pas perdre le temps qu'il a réservé."""
    monkeypatch.setattr(db, "HEARTBEAT_TIMEOUT", 1)
    deux_workers()
    utilisateur = db.create_user("etudiant", "hash")
    identifiant = db.create_instance(utilisateur, "abc123456789", "insacloud_etudiant_a1",
                                     8001, 30, os_type="alpine", term_port=8101,
                                     worker="worker1")
    avant = db.get_instance(identifiant)
    time.sleep(1.2)
    db.worker_heartbeat("worker2")
    faucheur.surveiller_workers()
    apres = db.get_instance(identifiant)
    assert apres["rental_id"] == avant["rental_id"]
    assert apres["expires_at"] == avant["expires_at"]
    assert apres["rental_status"] == "ACTIVE"


def test_sans_worker_de_repli_la_machine_est_marquee_arretee(docker_espion, monkeypatch):
    """Mieux vaut annoncer une machine arrêtée que la laisser paraître active."""
    monkeypatch.setattr(db, "HEARTBEAT_TIMEOUT", 1)
    db.register_worker("worker1", "192.168.56.11")
    utilisateur = db.create_user("etudiant", "hash")
    identifiant = db.create_instance(utilisateur, "abc123456789", "insacloud_etudiant_a1",
                                     8001, 30, os_type="alpine", term_port=8101,
                                     worker="worker1")
    time.sleep(1.2)
    faucheur.surveiller_workers()
    assert db.get_instance(identifiant)["status"] == db.STATUS_STOPPED


def test_un_parc_en_bonne_sante_ne_declenche_rien(docker_espion):
    deux_workers()
    assert faucheur.surveiller_workers() == 0
    assert docker_espion == []


def test_reprise_desactivable(docker_espion, monkeypatch):
    """INSACLOUD_AUTO_RECOVERY=0 laisse l'exploitant décider lui-même."""
    monkeypatch.setattr(db, "HEARTBEAT_TIMEOUT", 1)
    monkeypatch.setattr(faucheur, "AUTO_RECOVERY", False)
    deux_workers()
    utilisateur = db.create_user("etudiant", "hash")
    db.create_instance(utilisateur, "abc123456789", "insacloud_etudiant_a1", 8001, 30,
                       os_type="alpine", term_port=8101, worker="worker1")
    time.sleep(1.2)
    db.worker_heartbeat("worker2")
    assert faucheur.surveiller_workers() == 0
    assert not any(a["commande"] == "run" for a in docker_espion)


# --- API du parc -------------------------------------------------------------
def test_api_workers_liste_le_parc(client):
    deux_workers()
    donnees = client.get("/workers").get_json()
    assert donnees["count"] == 2
    assert {w["hostname"] for w in donnees["workers"]} == {"worker1", "worker2"}


def test_api_enregistrement_refuse_sans_jeton(client):
    reponse = client.post("/workers/register", json={"hostname": "pirate", "ip": "1.2.3.4"})
    assert reponse.status_code in (401, 503)
    assert db.get_worker_by_hostname("pirate") is None


def test_api_enregistrement_accepte_avec_jeton(client, monkeypatch):
    import api
    monkeypatch.setattr(api, "AGENT_TOKEN", "jeton-de-test")
    reponse = client.post("/workers/register",
                          json={"hostname": "worker9", "ip": "192.168.56.19",
                                "cpu": 2, "memory": 2048, "docker": True},
                          headers={"X-Agent-Token": "jeton-de-test"})
    assert reponse.status_code == 201
    assert db.get_worker_by_hostname("worker9")["status"] == WORKER_AVAILABLE


def test_api_refuse_un_noeud_sans_docker(client, monkeypatch):
    import api
    monkeypatch.setattr(api, "AGENT_TOKEN", "jeton-de-test")
    reponse = client.post("/workers/register",
                          json={"hostname": "worker9", "ip": "192.168.56.19", "docker": False},
                          headers={"X-Agent-Token": "jeton-de-test"})
    assert reponse.status_code == 400


def test_api_sante(client):
    donnees = client.get("/health").get_json()
    assert donnees["status"] == "ok"
    assert donnees["database"] == "ok"
    assert "instances_running" in donnees


def test_api_instances_exige_une_connexion(client):
    assert client.get("/instances", headers={"Accept": "application/json"}).status_code == 401


def test_api_location_complete(client, docker_espion, monkeypatch):
    """POST /rent crée la machine et ne révèle le mot de passe qu'une fois."""
    monkeypatch.setattr(services, "_choisir_worker", lambda: "worker1")
    db.register_worker("worker1", "192.168.56.11", cpu=2, memory=3072)
    inscrire(client)
    reponse = client.post("/rent", json={"distribution": "alpine",
                                         "mode": "terminal", "duration": 15})
    assert reponse.status_code == 201
    corps = reponse.get_json()
    assert corps["distribution"] == "alpine" and corps["worker"] == "worker1"
    assert len(corps["root_password"]) == 16
    assert corps["ssh_command"].startswith("ssh root@192.168.56.11 -p ")

    liste = client.get("/instances").get_json()
    assert liste["count"] == 1
    assert "root_password" not in liste["instances"][0], \
        "le mot de passe ne doit jamais réapparaître dans la liste"


def test_api_arret_d_une_machine(client, docker_espion, monkeypatch):
    monkeypatch.setattr(services, "_choisir_worker", lambda: "worker1")
    db.register_worker("worker1", "192.168.56.11", cpu=2, memory=3072)
    inscrire(client)
    identifiant = client.post("/rent", json={"duration": 10, "distribution": "alpine"}).get_json()["id"]
    reponse = client.post(f"/instances/{identifiant}/stop", json={})
    assert reponse.status_code == 200
    assert db.get_instance(identifiant)["status"] == db.STATUS_STOPPED
