"""
docker_ops.py - Tout ce qui parle au démon Docker.

Le sujet impose de créer les machines par la commande
    docker run -d --restart=always -p <port>:22 <image>
C'est donc la ligne de commande qui est pilotée, via `subprocess`, et non un
SDK : chaque option passée est visible telle quelle dans le code.

Ce module ne connaît ni Flask ni la base de données. Il est donc testable sans
serveur et réutilisable par l'application comme par le Faucheur.
"""

import logging
import os
import secrets
import socket
import string
import subprocess  # nosec B404 - le sujet impose de piloter Docker par la CLI

import workers as wk
from config import (AVAILABLE_IMAGES, CONTAINER_CPUS, CONTAINER_PIDS_LIMIT,
                    CONTAINER_PREFIX, DOCKER_TIMEOUT, GUI_CONTAINER_PORT, MODES,
                    PORT_MAX, PORT_MIN, TERM_CONTAINER_PORT, TLS_DIR)

log = logging.getLogger("insacloud.docker")

# Durcissement des conteneurs loués : capacités minimales pour sshd / supervisord,
# pas d'escalade de privilèges, limites de processus et de CPU.
CONTAINER_HARDENING = [
    "--cap-drop", "ALL",
    "--cap-add", "CHOWN", "--cap-add", "DAC_OVERRIDE", "--cap-add", "FOWNER",
    "--cap-add", "FSETID", "--cap-add", "SETGID", "--cap-add", "SETUID",
    "--cap-add", "SYS_CHROOT", "--cap-add", "NET_BIND_SERVICE",
    "--cap-add", "KILL", "--cap-add", "AUDIT_WRITE",
    "--security-opt", "no-new-privileges:true",
    "--pids-limit", CONTAINER_PIDS_LIMIT,
    "--cpus", CONTAINER_CPUS,
]


class DockerError(Exception):
    """Docker a refusé la commande, ou n'a pas répondu."""


def run_docker(*args, worker: str = "local", timeout: int = DOCKER_TIMEOUT) -> str:
    """Exécute `docker <args>` sur le nœud `worker` (local ou distant via SSH)."""
    cmd = [*wk.docker_command(worker), *args]
    try:
        # Appel sans shell : les arguments sont passés en liste, donc jamais
        # ré-interprétés par un interpréteur de commandes. Les seules valeurs
        # d'origine utilisateur (durée, distribution, mode) sont validées en amont.
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)  # nosec B603  # noqa: S603
    except FileNotFoundError as erreur:
        # « from » conserve la cause d'origine dans la trace : indispensable
        # pour distinguer une vraie panne d'une erreur dans le traitement.
        raise DockerError("La commande 'docker' est introuvable sur le serveur.") from erreur
    except subprocess.TimeoutExpired as erreur:
        raise DockerError("Docker n'a pas répondu dans le délai imparti.") from erreur
    if res.returncode != 0:
        message = res.stderr.strip() or f"docker {args[0]} a échoué (code {res.returncode})"
        raise DockerError(message)
    return res.stdout.strip()


# =============================================================================
#  Images
# =============================================================================
def image_for(distro: str, mode: str) -> str:
    """insacloud_<distro>[_desktop]:latest, surchargeable par INSACLOUD_IMAGE_<DISTRO>[_DESKTOP]."""
    suffix = "_desktop" if MODES[mode]["gui"] else ""
    default = f"insacloud_{distro}{suffix}:latest"
    return os.environ.get(f"INSACLOUD_IMAGE_{distro.upper()}{suffix.upper()}", default)


def image_available(distro: str, mode: str) -> bool:
    """Faux si Ansible a déployé la plateforme en mode démonstration sans cette image."""
    if not AVAILABLE_IMAGES:
        return True
    return image_for(distro, mode).split(":")[0] in AVAILABLE_IMAGES


# =============================================================================
#  Cycle de vie d'une machine louée
# =============================================================================
def docker_run_container(port: int, name: str, root_password: str, os_type: str,
                         mode: str, term_port: int, gui_port: int = None,
                         ssh_public_key: str = None, worker: str = "local") -> str:
    """
    Crée et démarre une machine louée. Retourne l'ID court du conteneur.

    Commande imposée : docker run -d --restart=always -p <port>:22 <image>
    complétée par le terminal web, le bureau (mode desktop), le durcissement,
    le mot de passe (jamais journalisé) et la clé SSH éventuelle.
    """
    spec = MODES[mode]
    args = [
        "run", "-d",
        "--restart=always",
        "-p", f"{port}:22",
        "-p", f"{term_port}:{TERM_CONTAINER_PORT}",
        "--name", name,
        "-e", f"ROOT_PASSWORD={root_password}",
        *CONTAINER_HARDENING,
    ]
    if ssh_public_key:
        args += ["-e", f"SSH_PUBKEY={ssh_public_key}"]
    if TLS_DIR:
        args += ["-v", f"{TLS_DIR}:/tls:ro"]
    if spec["gui"] and gui_port:
        args += ["-p", f"{gui_port}:{GUI_CONTAINER_PORT}", "--shm-size", "512m"]
    if spec["memory"]:
        # Le cours impose de définir réservation ET limite : la réservation est
        # le minimum garanti en cas de contention, la limite le plafond dur.
        args += ["--memory", spec["memory"], "--memory-reservation", spec["reservation"]]
    args.append(image_for(os_type, mode))
    return run_docker(*args, worker=worker)[:12]


def docker_remove_container(container_id: str, worker: str = "local") -> None:
    try:
        run_docker("rm", "-f", container_id, worker=worker)
    except DockerError as exc:
        if "No such container" in str(exc):
            return
        raise


def docker_container_state(container_id: str, worker: str = "local") -> str:
    try:
        return run_docker("inspect", "-f", "{{.State.Status}}", container_id,
                          worker=worker, timeout=15)
    except DockerError:
        return "absent"


def docker_rotate_password(container_id: str, new_password: str, worker: str = "local") -> None:
    """Rotation à chaud : SSH, terminal web et VNC prennent le nouveau mot de passe."""
    run_docker("exec", container_id, "/usr/local/bin/entrypoint.sh", "setpass",
               new_password, worker=worker)


def docker_stats(worker: str = "local") -> list:
    """
    Consommation CPU/mémoire des machines louées d'un nœud.
    Alimente le tableau de supervision et le watchdog.
    """
    try:
        sortie = run_docker("stats", "--no-stream", "--format",
                            "{{.Name}};{{.CPUPerc}};{{.MemUsage}}", worker=worker, timeout=20)
    except DockerError:
        return []
    mesures = []
    for ligne in sortie.splitlines():
        morceaux = ligne.split(";")
        if len(morceaux) == 3 and morceaux[0].startswith(CONTAINER_PREFIX):
            mesures.append({"name": morceaux[0], "cpu": morceaux[1], "memory": morceaux[2]})
    return mesures


# =============================================================================
#  Ports et secrets
# =============================================================================
def port_is_free_on_host(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            # Simple sonde : on vérifie que le port est libre sur toutes les
            # interfaces, puis la socket est refermée immédiatement. Aucun
            # service n'est mis en écoute ici.
            sock.bind(("0.0.0.0", port))  # nosec B104  # noqa: S104
            return True
        except OSError:
            return False


def choose_random_port(exclude: set = frozenset(), worker: str = "local",
                       port_occupe=None) -> int:
    """
    Port libre sur le nœud : non réservé en base, et (nœud local) réellement
    libre sur l'hôte. `port_occupe` est injecté par l'appelant pour éviter que
    ce module dépende de la base de données.
    """
    local = wk.worker_host(worker) is None
    for _ in range(100):
        # secrets (CSPRNG) plutôt que random : un port prévisible faciliterait
        # le balayage ciblé des machines fraîchement louées.
        port = PORT_MIN + secrets.randbelow(PORT_MAX - PORT_MIN + 1)
        if port in exclude:
            continue
        if port_occupe is not None and port_occupe(port, worker):
            continue
        if local and not port_is_free_on_host(port):
            continue
        return port
    raise DockerError("Aucun port libre disponible dans la plage configurée.")


def generate_password(length: int = 16) -> str:
    """Mot de passe root aléatoire (CSPRNG), sans caractères ambigus : ~90 bits d'entropie."""
    alphabet = "".join(c for c in string.ascii_letters + string.digits if c not in "0O1lI")
    return "".join(secrets.choice(alphabet) for _ in range(length))


def container_name_for(username: str) -> str:
    """Nom unique et lisible : insacloud_<utilisateur>_<6 caractères aléatoires>."""
    return f"{CONTAINER_PREFIX}{username}_{secrets.token_hex(3)}"
