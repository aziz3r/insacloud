"""
test_archives_purge.py - Effacement des archives périmées.

Une archive est une copie des fichiers d'un utilisateur : elle ne doit pas
rester sur le disque au-delà de sa durée de conservation. Et la fonction qui
efface ne doit jamais pouvoir effacer autre chose qu'une archive.
"""

import os
import time

import pytest

import archives
import database as db
import faucheur
import services


@pytest.fixture
def dossier(tmp_path, monkeypatch):
    monkeypatch.setattr(archives, "ARCHIVE_DIR", str(tmp_path))
    return tmp_path


def archive(dossier, jours, nom="a.tar.gz", statut="ok", port=8001):
    """Archive avec son fichier ; `jours` négatif = déjà périmée. Retourne (id, chemin du fichier)."""
    utilisateur = db.create_user(f"etudiant{port}", "pbkdf2:sha256:1$x$y")
    machine = db.create_instance(utilisateur, f"cid{port}", f"insacloud_m_{port}", port, 30,
                                 "ubuntu", "terminal", port + 100, gui_port=None, worker="local")
    resultat = {"status": statut}
    fichier = None
    if statut == "ok":
        fichier = dossier / str(utilisateur) / nom
        fichier.parent.mkdir(parents=True, exist_ok=True)
        fichier.write_bytes(b"donnees")
        resultat.update(path=f"{utilisateur}/{nom}", size=7, sha256="ab" * 32)
    return db.create_archive(utilisateur, machine, resultat, retention_days=jours), fichier


# --- supprimer_fichier -------------------------------------------------------
def test_supprime_un_fichier_d_archive(dossier):
    _, fichier = archive(dossier, 7)
    assert archives.supprimer_fichier(f"{fichier.parent.name}/{fichier.name}") is True
    assert not fichier.exists()


def test_fichier_deja_absent_compte_comme_efface(dossier):
    assert archives.supprimer_fichier("1/inexistant.tar.gz") is True


def test_refuse_d_effacer_hors_du_dossier(dossier, tmp_path_factory):
    dehors = tmp_path_factory.mktemp("ailleurs") / "important.txt"
    dehors.write_bytes(b"ne pas toucher")

    assert archives.supprimer_fichier("../" + dehors.parent.name + "/important.txt") is False
    assert dehors.exists(), "un chemin piégé ne doit jamais effacer un fichier extérieur"


def test_refuse_d_effacer_le_dossier_lui_meme(dossier):
    assert archives.supprimer_fichier(".") is False
    assert dossier.exists()


# --- nettoyer_partiels -------------------------------------------------------
def test_partiel_ancien_efface_partiel_recent_conserve(dossier):
    (dossier / "1").mkdir()
    vieux, recent, entier = (dossier / "1" / n for n in ("v.tar.gz.part", "r.tar.gz.part", "ok.tar.gz"))
    for fichier in (vieux, recent, entier):
        fichier.write_bytes(b"x")
    ancien = time.time() - 7200
    os.utime(vieux, (ancien, ancien))
    os.utime(entier, (ancien, ancien))

    assert archives.nettoyer_partiels(age_max=3600) == 1
    assert not vieux.exists()
    assert recent.exists(), "un .part récent est peut-être en cours d'écriture"
    assert entier.exists(), "une archive terminée n'est jamais un partiel"


# --- purger_archives_perimees ------------------------------------------------
def test_archive_perimee_effacee_fichier_et_ligne(dossier):
    identifiant, fichier = archive(dossier, jours=-1)

    assert services.purger_archives_perimees() == 1
    assert not fichier.exists()
    assert db.get_expired_archives() == []
    assert identifiant not in [a["id"] for a in db.get_expired_archives()]


def test_archive_valide_conservee(dossier):
    _, fichier = archive(dossier, jours=7)

    assert services.purger_archives_perimees() == 0
    assert fichier.exists()


def test_ligne_d_un_archivage_rate_purgee_sans_fichier(dossier):
    archive(dossier, jours=-1, statut="failed")

    assert services.purger_archives_perimees() == 1
    assert db.get_expired_archives() == []


def test_fichier_impossible_a_effacer_garde_sa_ligne(dossier, monkeypatch):
    archive(dossier, jours=-1)
    monkeypatch.setattr(archives, "supprimer_fichier", lambda chemin: False)

    assert services.purger_archives_perimees() == 0
    assert len(db.get_expired_archives()) == 1, "on réessaiera au prochain passage"


# --- Le Faucheur déclenche la purge ------------------------------------------
def test_le_faucheur_purge_a_chaque_reconciliation(monkeypatch):
    appels = []
    monkeypatch.setattr(faucheur, "RECONCILE_EVERY", 1)
    monkeypatch.setattr(faucheur, "reconcile", lambda: None)
    monkeypatch.setattr(services, "purger_archives_perimees", lambda: appels.append(1) or 0)
    monkeypatch.setattr(faucheur._stop_event, "wait", lambda delai: faucheur._stop_event.set())
    faucheur._stop_event.clear()
    try:
        faucheur.run_forever()
    finally:
        faucheur._stop_event.clear()

    assert appels == [1]
