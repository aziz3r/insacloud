"""
test_docker.py - Vérifie la commande envoyée au démon Docker et la répartition
sur les workers, sans jamais lancer de conteneur : `run_docker` est remplacé
par une fonction espion qui se contente d'enregistrer ses arguments.

Cela permet de contrôler en intégration continue, sur une machine sans Docker,
que la commande imposée par le sujet est bien respectée :
    docker run -d --restart=always -p <port>:22 <image>
"""

import pytest

import config
import docker_ops as docker
import workers as wk
from app import parse_int


@pytest.fixture
def commande(monkeypatch):
    """Capture les arguments passés à `run_docker` et renvoie la liste obtenue."""
    captures = {}

    def espion(*args, worker="local", timeout=None):
        captures["args"] = list(args)
        captures["worker"] = worker
        return "0123456789abcdef0123"

    monkeypatch.setattr(docker, "run_docker", espion)
    return captures


def lancer(commande, **kwargs):
    parametres = dict(port=8042, name="insacloud_etudiant_1", root_password="Secret123456",
                      os_type="ubuntu", mode="terminal", term_port=8142)
    parametres.update(kwargs)
    identifiant = docker.docker_run_container(**parametres)
    return commande["args"], identifiant


# --- Commande imposée par le sujet ------------------------------------------
def test_commande_imposee_par_le_sujet(commande):
    args, _ = lancer(commande)
    assert args[0] == "run"
    assert "-d" in args, "le conteneur doit tourner en arrière-plan"
    assert "--restart=always" in args, "exigence du sujet : redémarrage automatique"
    assert "8042:22" in args, "le port SSH aléatoire doit être publié vers le port 22"


def test_identifiant_court_retourne(commande):
    _, identifiant = lancer(commande)
    assert len(identifiant) == 12, "l'ID court Docker fait 12 caractères"


def test_terminal_web_publie(commande):
    args, _ = lancer(commande)
    assert f"8142:{config.TERM_CONTAINER_PORT}" in args


# --- Limites de ressources : réservation ET limite --------------------------
def test_mode_terminal_reserve_et_limite_la_memoire(commande):
    args, _ = lancer(commande, mode="terminal")
    assert args[args.index("--memory") + 1] == config.CONTAINER_MEMORY
    assert args[args.index("--memory-reservation") + 1] == config.CONTAINER_RESERVATION


def test_mode_bureau_reserve_davantage(commande):
    args, _ = lancer(commande, mode="desktop", gui_port=8242)
    assert args[args.index("--memory") + 1] == config.GUI_CONTAINER_MEMORY
    assert args[args.index("--memory-reservation") + 1] == config.GUI_CONTAINER_RESERVATION
    assert f"8242:{config.GUI_CONTAINER_PORT}" in args
    assert "--shm-size" in args, "le bureau graphique a besoin de mémoire partagée"


def test_reservation_inferieure_a_la_limite():
    """Une réservation supérieure à la limite serait refusée par Docker."""
    def en_octets(valeur):
        unites = {"m": 1024 ** 2, "g": 1024 ** 3}
        return int(valeur[:-1]) * unites[valeur[-1].lower()]

    for mode in config.MODES.values():
        assert en_octets(mode["reservation"]) < en_octets(mode["memory"])


def test_limites_cpu_et_processus(commande):
    args, _ = lancer(commande)
    assert args[args.index("--cpus") + 1] == config.CONTAINER_CPUS
    assert args[args.index("--pids-limit") + 1] == config.CONTAINER_PIDS_LIMIT


# --- Durcissement ------------------------------------------------------------
def test_capacites_abandonnees_puis_reaccordees_au_minimum(commande):
    args, _ = lancer(commande)
    assert args[args.index("--cap-drop") + 1] == "ALL"
    accordees = {args[i + 1] for i, a in enumerate(args) if a == "--cap-add"}
    assert "SYS_ADMIN" not in accordees and "NET_ADMIN" not in accordees
    assert {"CHOWN", "SETUID", "SETGID"} <= accordees, "sshd en a besoin"


def test_escalade_de_privileges_interdite(commande):
    args, _ = lancer(commande)
    assert "no-new-privileges:true" in args


def test_le_conteneur_n_est_pas_privilegie(commande):
    args, _ = lancer(commande)
    assert "--privileged" not in args
    assert not any(a.startswith("/var/run/docker.sock") for a in args)


# --- Clé SSH et TLS ----------------------------------------------------------
def test_cle_publique_transmise_au_conteneur(commande):
    cle = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIJhTest moi@insa"
    args, _ = lancer(commande, ssh_public_key=cle)
    assert f"SSH_PUBKEY={cle}" in args


def test_sans_cle_ssh_aucune_variable_vide(commande):
    args, _ = lancer(commande)
    assert not any(str(a).startswith("SSH_PUBKEY=") for a in args)


def test_image_choisie_selon_distribution_et_mode(commande):
    args, _ = lancer(commande, os_type="alpine", mode="terminal")
    assert args[-1] == "insacloud_alpine:latest"
    args, _ = lancer(commande, os_type="debian", mode="desktop", gui_port=8242)
    assert args[-1] == "insacloud_debian_desktop:latest"


# --- Répartition sur les workers --------------------------------------------
def test_mode_mono_hote_par_defaut():
    assert wk.load_workers() == {"local": None}


def test_lecture_de_la_configuration_des_workers(monkeypatch):
    monkeypatch.setenv("INSACLOUD_WORKERS", "worker1=192.168.56.11,worker2=192.168.56.12")
    assert wk.load_workers() == {"worker1": "192.168.56.11", "worker2": "192.168.56.12"}


def test_configuration_malformee_ignoree(monkeypatch):
    monkeypatch.setenv("INSACLOUD_WORKERS", "worker1=192.168.56.11, ,sans-hote=,")
    assert wk.load_workers() == {"worker1": "192.168.56.11"}


def test_commande_distante_par_ssh(monkeypatch):
    monkeypatch.setattr(wk, "WORKERS", {"worker1": "192.168.56.11", "local": None})
    assert wk.docker_command("worker1") == ["docker", "-H", "ssh://insacloud@192.168.56.11"]
    assert wk.docker_command("local") == ["docker"]


def test_repartition_sur_le_noeud_le_moins_charge(monkeypatch):
    monkeypatch.setattr(wk, "WORKERS", {"worker1": "192.168.56.11", "worker2": "192.168.56.12"})
    assert wk.pick_worker({"worker1": 3, "worker2": 1}) == "worker2"
    assert wk.pick_worker({"worker1": 0, "worker2": 0}) == "worker1", "à égalité : le premier"
    assert wk.pick_worker({}) == "worker1"


# --- Robustesse des entrées numériques --------------------------------------
@pytest.mark.parametrize("entree,attendu", [
    ("30", 30), ("", 0), (None, 0), ("abc", 0), ("-5", -5), ("1e9", 0),
])
def test_conversion_entiere_tolerante(entree, attendu):
    assert parse_int(entree) == attendu
