"""
test_archives_web.py - Téléchargement des archives depuis le site.

Une archive est une copie des fichiers d'un utilisateur : le point qui compte
est qu'elle ne soit servie qu'à son propriétaire, et jamais en dehors du
dossier des archives.
"""

import pytest

import app as insacloud
import database as db
from conftest import inscrire, pose_csrf

CONTENU = b"contenu-secret-de-alice"
JSON = {"Accept": "application/json"}


@pytest.fixture
def dossier(tmp_path, monkeypatch):
    monkeypatch.setattr(insacloud, "ARCHIVE_DIR", str(tmp_path))
    return tmp_path


def archive_de(nom_utilisateur, dossier, statut="ok", chemin=None, ecrire=True):
    """Crée un utilisateur, sa machine et son archive (fichier compris) ; retourne (user_id, archive_id)."""
    user_id = db.get_user_by_username(nom_utilisateur)["id"]
    machine = db.create_instance(user_id, "aaa111222333", f"insacloud_{nom_utilisateur}_1", 8002, 60,
                                 "ubuntu", "terminal", 8102, gui_port=None, worker="local")
    chemin = chemin or f"{user_id}/{machine}-abcd.tar.gz"
    if ecrire and statut == "ok":
        fichier = dossier / chemin
        fichier.parent.mkdir(parents=True, exist_ok=True)
        fichier.write_bytes(CONTENU)
    resultat = {"status": statut}
    if statut == "ok":
        resultat.update(path=chemin, size=len(CONTENU), sha256="ab" * 32)
    return user_id, db.create_archive(user_id, machine, resultat, retention_days=7)


def test_le_proprietaire_telecharge_son_archive(client, dossier):
    inscrire(client, "alice")
    _, archive_id = archive_de("alice", dossier)

    reponse = client.get(f"/archives/{archive_id}/telecharger")

    assert reponse.status_code == 200
    assert reponse.data == CONTENU
    assert "attachment" in reponse.headers["Content-Disposition"]
    assert "insacloud_alice_1.tar.gz" in reponse.headers["Content-Disposition"]


def test_l_archive_d_un_autre_compte_est_introuvable(client, dossier):
    inscrire(client, "alice")
    _, archive_id = archive_de("alice", dossier)
    client.post("/logout", data={"_csrf": pose_csrf(client)})

    inscrire(client, "bob")
    reponse = client.get(f"/archives/{archive_id}/telecharger", headers=JSON)

    assert reponse.status_code == 404
    assert CONTENU not in reponse.data


def test_telechargement_refuse_sans_connexion(client, dossier):
    inscrire(client, "alice")
    _, archive_id = archive_de("alice", dossier)
    client.post("/logout", data={"_csrf": pose_csrf(client)})

    reponse = client.get(f"/archives/{archive_id}/telecharger")

    assert reponse.status_code == 302 and "/login" in reponse.headers["Location"]
    assert CONTENU not in reponse.data


@pytest.mark.parametrize("statut", ["failed", "too_large"])
def test_archive_en_echec_non_telechargeable(client, dossier, statut):
    inscrire(client, "alice")
    _, archive_id = archive_de("alice", dossier, statut=statut)

    assert client.get(f"/archives/{archive_id}/telecharger", headers=JSON).status_code == 404


def test_chemin_hors_du_dossier_refuse_meme_si_la_base_est_alteree(client, dossier, tmp_path_factory):
    """Défense en profondeur : « ../ » dans le chemin enregistré ne doit jamais sortir du dossier."""
    secret = tmp_path_factory.mktemp("ailleurs") / "secret.txt"
    secret.write_bytes(b"mot-de-passe-du-serveur")
    inscrire(client, "alice")
    chemin_piege = "../" + secret.parent.name + "/secret.txt"
    _, archive_id = archive_de("alice", dossier, chemin=chemin_piege, ecrire=False)

    reponse = client.get(f"/archives/{archive_id}/telecharger", headers=JSON)

    assert reponse.status_code == 404
    assert b"mot-de-passe-du-serveur" not in reponse.data


def test_fichier_disparu_du_disque_repond_404(client, dossier):
    inscrire(client, "alice")
    _, archive_id = archive_de("alice", dossier, ecrire=False)
    # La ligne existe (statut ok) mais le fichier n'est plus là
    # (par exemple après une restauration partielle du serveur).
    assert client.get(f"/archives/{archive_id}/telecharger", headers=JSON).status_code == 404


def test_tableau_de_bord_liste_les_archives(client, dossier, monkeypatch):
    monkeypatch.setattr(insacloud, "docker_container_state", lambda *a, **k: "running")
    inscrire(client, "alice")
    _, archive_id = archive_de("alice", dossier)

    page = client.get("/dashboard").get_data(as_text=True)

    assert "Archives de mon travail" in page
    assert f"/archives/{archive_id}/telecharger" in page
    assert "insacloud_alice_1" in page


def test_tableau_de_bord_signale_un_archivage_echoue(client, dossier, monkeypatch):
    monkeypatch.setattr(insacloud, "docker_container_state", lambda *a, **k: "running")
    inscrire(client, "alice")
    _, archive_id = archive_de("alice", dossier, statut="failed")

    page = client.get("/dashboard").get_data(as_text=True)

    assert "Archivage échoué" in page
    assert f"/archives/{archive_id}/telecharger" not in page


def test_section_absente_sans_archive(client, dossier, monkeypatch):
    monkeypatch.setattr(insacloud, "docker_container_state", lambda *a, **k: "running")
    inscrire(client, "alice")
    assert "Archives de mon travail" not in client.get("/dashboard").get_data(as_text=True)


def test_taille_lisible():
    assert insacloud.taille_lisible(None) == "-"
    assert insacloud.taille_lisible(512) == "512 o"
    assert insacloud.taille_lisible(2048) == "2.0 Ko"
    assert insacloud.taille_lisible(5 * 1024 * 1024) == "5.0 Mo"
    assert insacloud.taille_lisible(3 * 1024 ** 3) == "3.0 Go"
