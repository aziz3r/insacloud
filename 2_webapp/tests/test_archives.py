"""
test_archives.py - Vérifie la sauvegarde du travail avant destruction.

Aucun conteneur réel : la commande `docker ...` est remplacée par un petit
programme Python qui joue le rôle de `tar` (il écrit des octets sur sa sortie
standard). C'est un vrai sous-processus, donc le flux, le code de retour, la
taille maximale et le délai sont éprouvés pour de bon.
"""

import hashlib
import os
import sys

import pytest

import archives


def fausse_commande(monkeypatch, script: str) -> None:
    """Remplace `docker ...` par `python -c <script>` (les arguments en trop sont ignorés)."""
    monkeypatch.setattr(archives.wk, "docker_command",
                        lambda worker="local": [sys.executable, "-c", script])


@pytest.fixture
def dossier(tmp_path, monkeypatch):
    monkeypatch.setattr(archives, "ARCHIVE_DIR", str(tmp_path))
    return tmp_path


def test_archive_ecrite_et_decrite(dossier, monkeypatch):
    fausse_commande(monkeypatch, "import sys; sys.stdout.buffer.write(b'mes fichiers')")
    res = archives.archiver_conteneur("abc123", "local", user_id=7, instance_id=3)

    assert res["status"] == "ok"
    assert res["size"] == len(b"mes fichiers")
    assert res["sha256"] == hashlib.sha256(b"mes fichiers").hexdigest()
    assert res["path"].startswith("7/3-") and res["path"].endswith(".tar.gz")
    assert (dossier / res["path"]).read_bytes() == b"mes fichiers"
    assert not list(dossier.rglob("*.part")), "aucun fichier partiel ne doit rester"


@pytest.mark.skipif(os.name != "posix", reason="les droits Unix n'existent pas sous Windows")
def test_archive_lisible_par_son_proprietaire_seulement(dossier, monkeypatch):
    fausse_commande(monkeypatch, "import sys; sys.stdout.buffer.write(b'secret')")
    res = archives.archiver_conteneur("abc123", "local", user_id=7, instance_id=3)
    assert (dossier / res["path"]).stat().st_mode & 0o077 == 0
    assert (dossier / "7").stat().st_mode & 0o077 == 0


def test_archive_trop_lourde_refusee_et_effacee(dossier, monkeypatch):
    monkeypatch.setattr(archives, "ARCHIVE_MAX_BYTES", 10)
    fausse_commande(monkeypatch, "import sys; sys.stdout.buffer.write(b'x' * 100000)")
    res = archives.archiver_conteneur("abc123", "local", user_id=7, instance_id=3)

    assert res["status"] == "too_large"
    assert not [f for f in dossier.rglob("*") if f.is_file()], "rien ne doit être conservé"


def test_tar_en_erreur_ne_laisse_aucun_fichier(dossier, monkeypatch):
    fausse_commande(monkeypatch, "import sys; sys.stdout.buffer.write(b'debut'); sys.exit(2)")
    res = archives.archiver_conteneur("abc123", "local", user_id=7, instance_id=3)

    assert res["status"] == "failed"
    assert not [f for f in dossier.rglob("*") if f.is_file()]


def test_tar_code_1_fichier_modifie_pendant_la_lecture_est_accepte(dossier, monkeypatch):
    fausse_commande(monkeypatch, "import sys; sys.stdout.buffer.write(b'ok'); sys.exit(1)")
    res = archives.archiver_conteneur("abc123", "local", user_id=7, instance_id=3)
    assert res["status"] == "ok"


def test_noeud_muet_arrete_apres_le_delai(dossier, monkeypatch):
    """Un worker qui ne répond plus ne doit pas bloquer le Faucheur."""
    monkeypatch.setattr(archives, "ARCHIVE_TIMEOUT", 1)
    fausse_commande(monkeypatch, "import time; time.sleep(30)")
    res = archives.archiver_conteneur("abc123", "local", user_id=7, instance_id=3)

    assert res["status"] == "failed"
    assert "délai" in res["detail"]


def test_commande_docker_introuvable_ne_leve_pas(dossier, monkeypatch):
    monkeypatch.setattr(archives.wk, "docker_command",
                        lambda worker="local": ["commande-inexistante-insacloud"])
    res = archives.archiver_conteneur("abc123", "local", user_id=7, instance_id=3)
    assert res["status"] == "failed"


def test_commande_envoyee_a_docker(dossier, monkeypatch):
    """La commande est `docker exec <id> tar czf - -C / <dossiers>` sur le bon nœud."""
    vues = {}

    def faux_popen(commande, **_):
        vues["commande"] = commande
        raise OSError("on s'arrête ici")

    monkeypatch.setattr(archives.wk, "docker_command", lambda worker="local": ["docker", "-H", f"ssh://{worker}"])
    monkeypatch.setattr(archives.subprocess, "Popen", faux_popen)
    archives.archiver_conteneur("abc123", "worker2", user_id=7, instance_id=3)

    assert vues["commande"][:5] == ["docker", "-H", "ssh://worker2", "exec", "abc123"]
    assert vues["commande"][5:] == ["tar", "czf", "-", "-C", "/", "root", "home"]


def test_chaque_utilisateur_a_son_dossier(dossier, monkeypatch):
    fausse_commande(monkeypatch, "import sys; sys.stdout.buffer.write(b'a')")
    a = archives.archiver_conteneur("c1", "local", user_id=1, instance_id=10)
    b = archives.archiver_conteneur("c2", "local", user_id=2, instance_id=10)
    assert a["path"].split("/")[0] == "1" and b["path"].split("/")[0] == "2"
