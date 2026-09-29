"""
services.py - Logique métier d'InsaCloud.

Un seul endroit décrit ce que veut dire « louer une machine », « l'arrêter »,
« la prolonger » ou « la remettre en service après la panne d'un worker ».
L'interface HTML (app.py), l'API JSON (api.py) et le Faucheur appellent ces
fonctions : le comportement est donc identique quel que soit le point d'entrée.

Chaque fonction renvoie un `Resultat` plutôt que de lever : les appelants
affichent un message ou renvoient un code HTTP sans avoir à traduire des
exceptions Docker.
"""

import logging
from dataclasses import dataclass, field

import database as db
import docker_ops as docker
import workers as wk
from config import (DEFAULT_MODE, DEFAULT_OS, MAX_DURATION_MINUTES,
                    MAX_INSTANCES_PER_USER, MIN_DURATION_MINUTES, MODES)
from crypto import encrypt_secret

log = logging.getLogger("insacloud.services")


@dataclass
class Resultat:
    """Issue d'une opération métier : succès ou message d'erreur exploitable."""

    ok: bool
    message: str = ""
    code: int = 200
    donnees: dict = field(default_factory=dict)

    @classmethod
    def echec(cls, message: str, code: int = 400) -> "Resultat":
        return cls(ok=False, message=message, code=code)

    @classmethod
    def succes(cls, message: str = "", **donnees) -> "Resultat":
        return cls(ok=True, message=message, donnees=donnees)


# =============================================================================
#  Catalogue : distributions et modes réellement proposables
# =============================================================================
def distributions_disponibles() -> dict:
    """{nom: fiche} des distributions actives dont l'image existe sur le serveur."""
    catalogue = {}
    for distribution in db.get_distributions():
        nom = distribution["name"]
        if any(docker.image_available(nom, mode) for mode in MODES):
            catalogue[nom] = {
                "label": distribution["label"] or nom,
                "hint": distribution["hint"] or "",
                "version": distribution["version"],
                "docker_image": distribution["docker_image"],
            }
    return catalogue


def modes_disponibles(distributions: dict = None) -> dict:
    distributions = distributions if distributions is not None else distributions_disponibles()
    return {nom: spec for nom, spec in MODES.items()
            if any(docker.image_available(d, nom) for d in distributions)}


# =============================================================================
#  Location d'une machine
# =============================================================================
def louer(user: dict, minutes: int, os_type: str = None, mode: str = None) -> Resultat:
    """
    Crée une machine pour cet utilisateur : choisit un worker, lance le
    conteneur, enregistre l'instance et la location. Nettoie derrière elle si
    une étape échoue, pour ne jamais laisser de conteneur orphelin.
    """
    os_type = os_type or DEFAULT_OS
    mode = mode or DEFAULT_MODE

    if not (MIN_DURATION_MINUTES <= minutes <= MAX_DURATION_MINUTES):
        return Resultat.echec(
            f"Durée invalide : choisissez entre {MIN_DURATION_MINUTES} et "
            f"{MAX_DURATION_MINUTES} minutes.")

    catalogue = distributions_disponibles()
    if mode not in MODES:
        return Resultat.echec("Mode inconnu.")
    if os_type not in catalogue:
        return Resultat.echec("Distribution inconnue ou indisponible sur ce serveur.")
    if not docker.image_available(os_type, mode):
        return Resultat.echec(
            f"{catalogue[os_type]['label']} en mode {MODES[mode]['label'].lower()} "
            f"n'est pas disponible sur ce serveur (mode démonstration).")

    if db.count_active_instances(user["id"]) >= MAX_INSTANCES_PER_USER:
        return Resultat.echec(
            f"Quota atteint : vous ne pouvez pas louer plus de "
            f"{MAX_INSTANCES_PER_USER} machines simultanément.", code=429)

    noeud = _choisir_worker()
    if noeud is None:
        return Resultat.echec(
            "Aucun nœud d'exécution n'est disponible pour le moment.", code=503)

    nom = docker.container_name_for(user["username"])
    mot_de_passe = docker.generate_password()
    creation = _creer_conteneur(nom, mot_de_passe, os_type, mode, noeud,
                                user.get("ssh_public_key"))
    if not creation.ok:
        return creation

    ports = creation.donnees
    try:
        instance_id = db.create_instance(
            user["id"], ports["container_id"], nom, ports["port"], minutes,
            os_type, mode, ports["term_port"], ports["gui_port"],
            root_password_enc=encrypt_secret(mot_de_passe), worker=noeud)
    except Exception as exc:  # noqa: BLE001 - toute erreur doit défaire le conteneur
        log.exception("Enregistrement impossible, suppression du conteneur %s",
                      ports["container_id"])
        _supprimer_sans_bruit(ports["container_id"], noeud)
        return Resultat.echec(f"Erreur interne lors de l'enregistrement : {exc}", code=500)

    log.info("Machine créée : %s (%s/%s, id=%s, nœud=%s, port=%s, %s min) pour '%s'",
             nom, os_type, mode, ports["container_id"], noeud, ports["port"],
             minutes, user["username"])
    return Resultat.succes(
        f"Machine « {nom} » ({catalogue[os_type]['label']}, "
        f"{MODES[mode]['label'].lower()}) louée pour {minutes} minute(s).",
        instance_id=instance_id, name=nom, password=mot_de_passe,
        worker=noeud, port=ports["port"], is_desktop=MODES[mode]["gui"])


def _choisir_worker() -> str:
    """
    Nom du worker qui accueillera la machine.

    Deux sources possibles : les workers enregistrés en base (agent + battement
    de cœur) ou, à défaut, ceux déclarés par configuration. La base fait foi dès
    qu'elle contient un nœud joignable, car elle seule connaît l'état réel.
    """
    candidat = db.select_available_worker(prefer=list(wk.WORKERS))
    if candidat is not None:
        return candidat["hostname"]
    configures = db.count_running_per_worker()
    return wk.pick_worker({nom: configures.get(nom, 0) for nom in wk.WORKERS})


def _creer_conteneur(nom: str, mot_de_passe: str, os_type: str, mode: str,
                     noeud: str, cle_ssh: str = None) -> Resultat:
    """
    Tire des ports libres et lance le conteneur. Sur un nœud distant les ports
    ne peuvent pas être sondés : en cas de conflit signalé par Docker, on
    retente avec d'autres ports (trois essais).
    """
    derniere_erreur = None
    for essai in range(3):
        try:
            port = docker.choose_random_port(worker=noeud, port_occupe=db.port_in_use)
            term = docker.choose_random_port(exclude={port}, worker=noeud,
                                             port_occupe=db.port_in_use)
            gui = (docker.choose_random_port(exclude={port, term}, worker=noeud,
                                             port_occupe=db.port_in_use)
                   if MODES[mode]["gui"] else None)
            container_id = docker.docker_run_container(
                port, nom, mot_de_passe, os_type, mode, term, gui, cle_ssh, noeud)
            return Resultat.succes(container_id=container_id, port=port,
                                   term_port=term, gui_port=gui)
        except docker.DockerError as exc:
            derniere_erreur = exc
            message = str(exc).lower()
            if "port is already allocated" in message or "address already in use" in message:
                log.warning("Conflit de port sur %s, nouvelle tentative (%d/3)", noeud, essai + 1)
                _supprimer_sans_bruit(nom, noeud)   # conteneur créé mais non démarré
                continue
            break
    log.error("Échec de création du conteneur %s sur %s : %s", nom, noeud, derniere_erreur)
    return Resultat.echec(f"Impossible de créer la machine : {derniere_erreur}", code=502)


def _supprimer_sans_bruit(identifiant: str, noeud: str) -> None:
    """Supprime sans propager d'erreur : chemin de nettoyage après un échec."""
    log.info("Nettoyage après échec : suppression de %s sur %s", identifiant, noeud)
    try:
        docker.docker_remove_container(identifiant, noeud)
    except docker.DockerError:
        pass


# =============================================================================
#  Cycle de vie d'une location
# =============================================================================
def arreter(instance_id: int, user_id: int) -> Resultat:
    """Détruit la machine et clôt la location. Idempotent."""
    instance = db.get_instance(instance_id, user_id)
    if instance is None:
        return Resultat.echec("Machine introuvable.", code=404)
    if instance["status"] != db.STATUS_RUNNING:
        return Resultat.succes("Cette machine est déjà arrêtée.")
    try:
        docker.docker_remove_container(instance["container_id"], instance["worker"])
    except docker.DockerError as exc:
        log.error("Suppression impossible de %s : %s", instance["container_name"], exc)
        return Resultat.echec(f"Impossible de détruire la machine : {exc}", code=502)
    db.set_instance_status(instance_id, db.STATUS_STOPPED)
    log.info("Machine détruite : %s (nœud=%s)", instance["container_name"], instance["worker"])
    return Resultat.succes(f"Machine « {instance['container_name']} » détruite.")


def prolonger(instance_id: int, user_id: int, minutes: int) -> Resultat:
    instance = db.get_instance(instance_id, user_id)
    if instance is None:
        return Resultat.echec("Machine introuvable.", code=404)
    if instance["status"] != db.STATUS_RUNNING:
        return Resultat.echec("Impossible de prolonger une machine arrêtée.")
    if not (MIN_DURATION_MINUTES <= minutes <= MAX_DURATION_MINUTES):
        return Resultat.echec("Durée de prolongation invalide.")
    db.extend_instance(instance_id, minutes)
    apres = db.get_instance(instance_id, user_id)
    log.info("Location prolongée de %s min : %s", minutes, instance["container_name"])
    return Resultat.succes(f"Location prolongée de {minutes} minute(s).",
                           expires_at=apres["expires_at"])


def regenerer_mot_de_passe(instance_id: int, user_id: int) -> Resultat:
    """Change le mot de passe root à chaud : SSH, terminal web et VNC suivent."""
    instance = db.get_instance(instance_id, user_id)
    if instance is None:
        return Resultat.echec("Machine introuvable.", code=404)
    if instance["status"] != db.STATUS_RUNNING:
        return Resultat.echec("Impossible sur une machine arrêtée.")
    nouveau = docker.generate_password()
    try:
        docker.docker_rotate_password(instance["container_id"], nouveau, instance["worker"])
    except docker.DockerError as exc:
        log.error("Rotation impossible sur %s : %s", instance["container_name"], exc)
        return Resultat.echec(f"Impossible de changer le mot de passe : {exc}", code=502)
    db.set_instance_password(instance_id, encrypt_secret(nouveau))
    log.info("Mot de passe régénéré : %s", instance["container_name"])
    return Resultat.succes("Nouveau mot de passe généré.",
                           password=nouveau, name=instance["container_name"],
                           is_desktop=bool(instance["gui_port"]))


# =============================================================================
#  Haute disponibilité : reprise des instances d'un worker en panne
# =============================================================================
def recreer_ailleurs(instance: dict) -> Resultat:
    """
    Recrée une instance sur un autre worker après la panne du sien.

    Le conteneur est reconstruit à partir de la même image, avec un mot de passe
    neuf — l'ancien vivait dans un conteneur devenu inaccessible. La location
    n'est pas touchée : l'utilisateur conserve la durée qu'il a payée.

    Ce que cette reprise ne rend pas : les fichiers créés dans l'ancienne
    machine, qui vivaient dans son système de fichiers. C'est la limite d'une
    reprise sans stockage partagé, et elle est assumée.
    """
    ancien_noeud = instance["worker"]
    candidat = db.select_available_worker()
    if candidat is None or candidat["hostname"] == ancien_noeud:
        return Resultat.echec(
            f"Aucun worker de repli disponible pour {instance['container_name']}.", code=503)
    nouveau_noeud = candidat["hostname"]

    utilisateur = db.get_user_by_id(instance["user_id"])
    mot_de_passe = docker.generate_password()
    nom = instance["container_name"]

    creation = _creer_conteneur(nom, mot_de_passe, instance["os_type"], instance["mode"],
                                nouveau_noeud, utilisateur.get("ssh_public_key")
                                if utilisateur else None)
    if not creation.ok:
        return creation

    ports = creation.donnees
    db.move_instance(instance["id"], ports["container_id"], nouveau_noeud,
                     ports["port"], ports["term_port"], ports["gui_port"])
    db.set_instance_password(instance["id"], encrypt_secret(mot_de_passe))
    log.warning("Instance %s reprise : %s -> %s (nouveau port %s)",
                nom, ancien_noeud, nouveau_noeud, ports["port"])
    return Resultat.succes(
        f"Machine « {nom} » reprise sur {nouveau_noeud}.",
        instance_id=instance["id"], worker=nouveau_noeud,
        port=ports["port"], password=mot_de_passe)


def reprendre_worker(hostname: str) -> list:
    """
    Reprend toutes les instances actives d'un worker déclaré hors ligne.
    Retourne la liste des résultats, un par instance.
    """
    resultats = []
    for instance in db.get_instances_on_worker(hostname, actives_seulement=True):
        resultat = recreer_ailleurs(instance)
        resultats.append((instance["container_name"], resultat))
        if not resultat.ok:
            # Le conteneur d'origine est injoignable : on marque l'instance
            # arrêtée plutôt que de la laisser paraître active.
            db.set_instance_status(instance["id"], db.STATUS_STOPPED)
    return resultats
