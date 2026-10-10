"""
test_archives_api.py - Liste JSON des archives (GET /archives).
"""

import database as db
from conftest import inscrire, pose_csrf


def une_archive(nom_utilisateur, statut="ok", port=8001):
    user_id = db.get_user_by_username(nom_utilisateur)["id"]
    machine = db.create_instance(user_id, f"cid{port}", f"insacloud_{nom_utilisateur}_{port}", port, 30,
                                 "ubuntu", "terminal", port + 100, gui_port=None, worker="local")
    resultat = {"status": statut}
    if statut == "ok":
        resultat.update(path=f"{user_id}/{machine}-secret-interne.tar.gz", size=2048, sha256="ab" * 32)
    return db.create_archive(user_id, machine, resultat, retention_days=7)


def test_liste_json_des_archives(client):
    inscrire(client, "alice")
    identifiant = une_archive("alice")

    reponse = client.get("/archives", headers={"Accept": "application/json"})

    assert reponse.status_code == 200
    donnees = reponse.get_json()
    assert donnees["count"] == 1
    archive = donnees["archives"][0]
    assert archive["id"] == identifiant
    assert archive["status"] == "ok"
    assert archive["size_bytes"] == 2048
    assert archive["machine"].startswith("insacloud_alice_")
    assert archive["download_url"] == f"/archives/{identifiant}/telecharger"


def test_le_chemin_du_serveur_n_est_jamais_expose(client):
    inscrire(client, "alice")
    une_archive("alice")

    texte = client.get("/archives", headers={"Accept": "application/json"}).get_data(as_text=True)

    assert "secret-interne" not in texte
    assert '"path"' not in texte


def test_pas_de_lien_pour_une_archive_en_echec(client):
    inscrire(client, "alice")
    une_archive("alice", statut="failed")

    archive = client.get("/archives", headers={"Accept": "application/json"}).get_json()["archives"][0]

    assert archive["status"] == "failed"
    assert archive["download_url"] is None


def test_chaque_compte_ne_voit_que_ses_archives(client):
    inscrire(client, "alice")
    une_archive("alice")
    client.post("/logout", data={"_csrf": pose_csrf(client)})

    inscrire(client, "bob")
    donnees = client.get("/archives", headers={"Accept": "application/json"}).get_json()

    assert donnees == {"count": 0, "archives": []}


def test_liste_refusee_sans_connexion(client):
    reponse = client.get("/archives", headers={"Accept": "application/json"})
    assert reponse.status_code == 401
