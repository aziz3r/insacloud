"""
test_archives_base.py - Vérifie la table des archives et sa migration.

Le point qui compte le plus ici est le cloisonnement : une archive est une
copie des fichiers d'un utilisateur, elle ne doit jamais être servie à un autre.
"""

from datetime import timedelta

import pytest
from alembic import command
from alembic.config import Config

import database as db
from models import ARCHIVE_FAILED, ARCHIVE_OK, ARCHIVE_TOO_LARGE, utc_now


def utilisateur(nom):
    return db.create_user(nom, "pbkdf2:sha256:1$x$y")


def machine(user_id, port=8001):
    return db.create_instance(user_id, f"cid{port}", f"insacloud_m_{port}", port, 30,
                              "ubuntu", "terminal", port + 100, gui_port=None, worker="local")


def archive_ok(chemin="1/1-aaaa.tar.gz"):
    return {"status": "ok", "path": chemin, "size": 1234, "sha256": "ab" * 32}


def test_archive_enregistree_et_listee():
    u = utilisateur("etudiant")
    m = machine(u)
    identifiant = db.create_archive(u, m, archive_ok(), retention_days=7)

    liste = db.get_user_archives(u)
    assert [a["id"] for a in liste] == [identifiant]
    assert liste[0]["status"] == ARCHIVE_OK
    assert liste[0]["size_bytes"] == 1234
    assert liste[0]["container_name"] == "insacloud_m_8001"
    assert liste[0]["expires_at"] > utc_now() + timedelta(days=6)


def test_archive_d_un_autre_compte_introuvable():
    a, b = utilisateur("alice"), utilisateur("bernard")
    identifiant = db.create_archive(a, machine(a), archive_ok(), retention_days=7)

    assert db.get_archive(identifiant, a) is not None
    assert db.get_archive(identifiant, b) is None, "cloisonnement entre comptes"
    assert db.get_user_archives(b) == []


@pytest.mark.parametrize("statut", [ARCHIVE_FAILED, ARCHIVE_TOO_LARGE])
def test_archive_en_echec_listee_mais_pas_telechargeable(statut):
    u = utilisateur("etudiant")
    identifiant = db.create_archive(u, machine(u), {"status": statut}, retention_days=7)

    assert db.get_user_archives(u)[0]["status"] == statut
    assert db.get_archive(identifiant, u) is None


def test_archive_perimee_masquee_et_signalee_a_purger():
    u = utilisateur("etudiant")
    identifiant = db.create_archive(u, machine(u), archive_ok(), retention_days=-1)

    assert db.get_user_archives(u) == []
    assert db.get_archive(identifiant, u) is None
    assert [a["id"] for a in db.get_expired_archives()] == [identifiant]

    db.delete_archive(identifiant)
    assert db.get_expired_archives() == []


def test_archive_valide_non_signalee_a_purger():
    u = utilisateur("etudiant")
    db.create_archive(u, machine(u), archive_ok(), retention_days=7)
    assert db.get_expired_archives() == []


def test_migration_et_modele_identiques(tmp_path, monkeypatch):
    """Équivalent de `alembic check` : la migration produit exactement le schéma du modèle."""
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'migration.db'}")
    configuration = Config(db.os.path.join(db.BASE_DIR, "alembic.ini"))
    configuration.set_main_option("script_location", db.os.path.join(db.BASE_DIR, "migrations"))
    configuration.attributes["configure_logger"] = False

    command.upgrade(configuration, "head")
    command.check(configuration)                   # lève une erreur si le modèle diffère
    command.downgrade(configuration, "-1")
    command.upgrade(configuration, "head")         # remonter après descente doit marcher
