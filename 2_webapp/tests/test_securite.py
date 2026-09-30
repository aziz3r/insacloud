"""
test_securite.py - Vérifie les protections de l'application.

Chaque test correspond à une exigence de sécurité annoncée dans le rapport :
politique de mots de passe, chiffrement au repos, CSRF, en-têtes HTTP,
anti-force-brute, coffre à secrets et cloisonnement entre comptes.
"""

import re

import app as insacloud
import crypto
import database as db
import docker_ops
from conftest import CSRF, GOOD_PASSWORD, inscrire, pose_csrf


# --- Politique de mots de passe ---------------------------------------------
def test_mot_de_passe_trop_court_refuse():
    assert insacloud.password_policy_error("Court1!", "etudiant") is not None


def test_mot_de_passe_courant_refuse():
    assert insacloud.password_policy_error("motdepasse", "etudiant") is not None


def test_mot_de_passe_egal_identifiant_refuse():
    assert insacloud.password_policy_error("etudiant-insa", "Etudiant-Insa") is not None


def test_mot_de_passe_conforme_accepte():
    assert insacloud.password_policy_error(GOOD_PASSWORD, "etudiant") is None


def test_inscription_refuse_un_mot_de_passe_faible(client):
    pose_csrf(client)
    reponse = client.post("/register", data={
        "_csrf": CSRF, "username": "etudiant", "password": "123456", "confirm": "123456"})
    assert reponse.status_code == 400
    assert db.get_user_by_username("etudiant") is None


def test_mot_de_passe_jamais_stocke_en_clair(client):
    inscrire(client)
    utilisateur = db.get_user_by_username("etudiant")
    assert GOOD_PASSWORD not in utilisateur["password_hash"]
    assert utilisateur["password_hash"].startswith(("pbkdf2:", "scrypt:", "argon2"))


# --- Chiffrement des mots de passe des machines (au repos) -------------------
def test_chiffrement_reversible_et_illisible():
    chiffre = crypto.encrypt_secret("motdepasse-machine")
    assert "motdepasse-machine" not in chiffre
    assert crypto.decrypt_secret(chiffre) == "motdepasse-machine"


def test_dechiffrement_d_un_jeton_invalide_ne_leve_pas():
    assert crypto.decrypt_secret("jeton-corrompu") is None
    assert crypto.decrypt_secret("") is None


# --- CSRF --------------------------------------------------------------------
def test_post_sans_jeton_csrf_refuse(client):
    """Sans jeton, la requête est abandonnée (403) puis redirigée vers la connexion."""
    reponse = client.post("/register", data={"username": "pirate", "password": GOOD_PASSWORD})
    assert reponse.status_code == 302
    assert "/login" in reponse.headers["Location"]
    assert db.get_user_by_username("pirate") is None, "aucun compte ne doit avoir été créé"


def test_post_avec_mauvais_jeton_csrf_refuse(client):
    pose_csrf(client)
    reponse = client.post("/register", data={"_csrf": "mauvais", "username": "pirate",
                                             "password": GOOD_PASSWORD, "confirm": GOOD_PASSWORD})
    assert reponse.status_code == 302
    assert "/login" in reponse.headers["Location"]
    assert db.get_user_by_username("pirate") is None


def test_action_sur_une_machine_refusee_sans_jeton_csrf(connecte):
    """Le jeton protège aussi les actions destructrices, pas seulement l'inscription."""
    utilisateur = db.get_user_by_username("etudiant")
    identifiant = db.create_instance(utilisateur["id"], "ccc333444555", "insacloud_etudiant_9",
                                     8009, 60, "ubuntu", "terminal", 8109,
                                     gui_port=None, worker="local")
    reponse = connecte.post(f"/instances/{identifiant}/delete", data={})
    assert reponse.status_code == 302
    assert db.get_instance(identifiant, utilisateur["id"]) is not None


# --- En-têtes HTTP de sécurité ----------------------------------------------
def test_entetes_de_securite_presents(client):
    entetes = client.get("/login").headers
    assert "default-src 'self'" in entetes["Content-Security-Policy"]
    assert "frame-ancestors 'none'" in entetes["Content-Security-Policy"]
    assert entetes["X-Content-Type-Options"] == "nosniff"
    assert entetes["X-Frame-Options"] == "DENY"
    assert entetes["Referrer-Policy"] == "no-referrer"
    assert entetes["Cache-Control"] == "no-store"


def test_csp_sans_unsafe_inline(client):
    """Une CSP stricte interdit tout script ou style en ligne : aucune injection ne s'exécute."""
    csp = client.get("/login").headers["Content-Security-Policy"]
    assert "unsafe-inline" not in csp and "unsafe-eval" not in csp


def test_cookie_de_session_httponly_et_samesite(client):
    inscrire(client)
    cookie = "".join(str(v) for v in client.get("/dashboard").headers.getlist("Set-Cookie"))
    session = insacloud.app.config
    assert session["SESSION_COOKIE_HTTPONLY"] is True
    assert session["SESSION_COOKIE_SAMESITE"] == "Strict"
    assert cookie == "" or "HttpOnly" in cookie


# --- Authentification et anti-force-brute -----------------------------------
def test_tableau_de_bord_inaccessible_sans_compte(client):
    reponse = client.get("/dashboard")
    assert reponse.status_code == 302
    assert "/login" in reponse.headers["Location"]


def test_mauvais_mot_de_passe_refuse(client):
    inscrire(client)
    client.post("/logout", data={"_csrf": pose_csrf(client)})
    pose_csrf(client)
    reponse = client.post("/login", data={"_csrf": CSRF, "username": "etudiant",
                                          "password": "mauvais-mot-de-passe"})
    assert reponse.status_code == 401


def test_verrouillage_apres_trop_d_echecs(client):
    inscrire(client)
    client.post("/logout", data={"_csrf": pose_csrf(client)})
    for _ in range(insacloud.LOGIN_MAX_FAILURES_USER):
        pose_csrf(client)
        client.post("/login", data={"_csrf": CSRF, "username": "etudiant", "password": "faux"})
    pose_csrf(client)
    reponse = client.post("/login", data={"_csrf": CSRF, "username": "etudiant",
                                          "password": GOOD_PASSWORD})
    assert reponse.status_code == 429, "le compte doit être verrouillé, même avec le bon mot de passe"


def test_deconnexion_impossible_en_get(client):
    inscrire(client)
    assert client.get("/logout").status_code == 405


def test_identifiant_invalide_refuse(client):
    pose_csrf(client)
    reponse = client.post("/register", data={"_csrf": CSRF, "username": "a b/c",
                                             "password": GOOD_PASSWORD, "confirm": GOOD_PASSWORD})
    assert reponse.status_code == 400


# --- Coffre à secrets --------------------------------------------------------
def test_coffre_ferme_par_defaut(connecte):
    with connecte.session_transaction() as session:
        assert "vault_until" not in session


def test_coffre_refuse_un_mauvais_mot_de_passe(connecte):
    connecte.post("/vault/unlock", data={"_csrf": CSRF, "password": "pas-le-bon"})
    with connecte.session_transaction() as session:
        assert "vault_until" not in session


def test_coffre_s_ouvre_avec_le_bon_mot_de_passe(connecte):
    connecte.post("/vault/unlock", data={"_csrf": CSRF, "password": GOOD_PASSWORD})
    with connecte.session_transaction() as session:
        assert session.get("vault_until", 0) > 0
    assert insacloud.VAULT_WINDOW_MINUTES > 0


def test_coffre_se_referme(connecte):
    connecte.post("/vault/unlock", data={"_csrf": CSRF, "password": GOOD_PASSWORD})
    connecte.post("/vault/lock", data={"_csrf": CSRF})
    with connecte.session_transaction() as session:
        assert "vault_until" not in session


def test_mot_de_passe_machine_masque_tant_que_le_coffre_est_ferme(connecte, monkeypatch):
    """Le tableau de bord ne doit jamais afficher un mot de passe quand le coffre est fermé."""
    monkeypatch.setattr(insacloud, "docker_container_state", lambda *a, **k: "running")
    utilisateur = db.get_user_by_username("etudiant")
    secret = "MotDePasseMachine123"
    identifiant = db.create_instance(utilisateur["id"], "abc123456789", "insacloud_etudiant_1",
                                     8001, 60, "ubuntu", "terminal", 8101, gui_port=None, worker="local")
    db.set_instance_password(identifiant, crypto.encrypt_secret(secret))

    page = connecte.get("/dashboard").get_data(as_text=True)
    assert secret not in page

    connecte.post("/vault/unlock", data={"_csrf": CSRF, "password": GOOD_PASSWORD})
    page = connecte.get("/dashboard").get_data(as_text=True)
    assert secret in page, "une fois le coffre ouvert, le mot de passe doit être visible"


# --- Cloisonnement entre comptes --------------------------------------------
def test_une_machine_n_est_pas_visible_par_un_autre_compte(client, monkeypatch):
    monkeypatch.setattr(insacloud, "docker_container_state", lambda *a, **k: "running")
    inscrire(client, "alice")
    alice = db.get_user_by_username("alice")
    db.create_instance(alice["id"], "aaa111222333", "insacloud_alice_1",
                       8002, 60, "ubuntu", "terminal", 8102, gui_port=None, worker="local")
    client.post("/logout", data={"_csrf": pose_csrf(client)})

    inscrire(client, "bob")
    page = client.get("/dashboard").get_data(as_text=True)
    assert "insacloud_alice_1" not in page


def test_suppression_d_une_machine_d_autrui_refusee(client, monkeypatch):
    monkeypatch.setattr(insacloud, "docker_container_state", lambda *a, **k: "running")
    inscrire(client, "alice")
    alice = db.get_user_by_username("alice")
    identifiant = db.create_instance(alice["id"], "aaa111222333", "insacloud_alice_1",
                                     8003, 60, "ubuntu", "terminal", 8103, gui_port=None, worker="local")
    client.post("/logout", data={"_csrf": pose_csrf(client)})

    inscrire(client, "bob")
    pose_csrf(client)
    reponse = client.post(f"/instances/{identifiant}/delete", data={"_csrf": CSRF})
    assert reponse.status_code in (403, 404) or db.get_instance(identifiant, alice["id"]) is not None


# --- Validation des entrées --------------------------------------------------
def test_expression_des_identifiants():
    valides = ["etudiant", "a1_b.c-d", "Insa42"]
    invalides = ["ab", "_debut", "a" * 33, "avec espace", "slash/interdit", ""]
    assert all(insacloud.USERNAME_RE.match(x) for x in valides)
    assert not any(insacloud.USERNAME_RE.match(x) for x in invalides)


def test_cle_publique_ssh_validee():
    valide = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIJhTestTestTestTestTestTestTestTestTest0 moi@insa"
    assert insacloud.SSH_PUBKEY_RE.match(valide)
    assert not insacloud.SSH_PUBKEY_RE.match("rm -rf / ; ssh-ed25519 AAAA")
    assert not insacloud.SSH_PUBKEY_RE.match("pas une clé")


def test_generation_de_mot_de_passe_solide():
    """Tirage uniforme dans un alphabet sans caractères ambigus, 16 caractères."""
    mots = [docker_ops.generate_password() for _ in range(200)]
    assert len(set(mots)) == 200, "deux mots de passe identiques révéleraient un générateur faible"
    assert all(len(m) == 16 for m in mots)
    assert not any(set(m) & set("0O1lI") for m in mots), "caractères ambigus exclus"
    assert all(re.fullmatch(r"[A-Za-z2-9]{16}", m) for m in mots)
    # 56 caractères possibles ^ 16 tirages -> environ 93 bits d'entropie
    alphabet = set("".join(mots))
    assert len(alphabet) >= 50, "le générateur doit couvrir tout l'alphabet"


def test_longueur_du_mot_de_passe_parametrable():
    assert len(docker_ops.generate_password(24)) == 24


# --- Le secret ne doit jamais transiter par le cookie ------------------------
def _decoder_cookie(valeur: str) -> str:
    """
    Décode la charge utile d'un cookie de session Flask SANS la clé secrète.

    C'est exactement ce que peut faire quiconque met la main sur le cookie :
    la session Flask est signée, ce qui garantit qu'elle n'a pas été modifiée,
    mais elle n'est pas chiffrée — son contenu est lisible.
    """
    import base64
    import zlib

    charge = valeur.split(".")[0]
    compresse = charge.startswith("-")
    if compresse:
        charge = charge[1:]
    charge += "=" * (-len(charge) % 4)
    brut = base64.urlsafe_b64decode(charge)
    if compresse:
        brut = zlib.decompress(brut)
    return brut.decode(errors="replace")


def test_le_mot_de_passe_machine_ne_transite_pas_par_le_cookie(connecte, monkeypatch):
    """
    Le mot de passe root est chiffré en base ; il ne doit pas ressortir en
    clair dans le cookie de session au moment de l'afficher.
    """
    monkeypatch.setattr(insacloud, "docker_container_state", lambda *a, **k: "running")
    utilisateur = db.get_user_by_username("etudiant")
    secret = "MotDePasseRoot12"
    identifiant = db.create_instance(utilisateur["id"], "abc123456789", "insacloud_etudiant_c1",
                                     8020, 30, os_type="ubuntu", mode="terminal",
                                     term_port=8120, gui_port=None, worker="local")
    db.set_instance_password(identifiant, crypto.encrypt_secret(secret))

    # On simule ce que fait la route de location : marquer la machine à révéler
    with connecte.session_transaction() as session:
        session["reveal"] = {"instance": identifiant, "rotated": False}

    reponse = connecte.get("/dashboard")
    cookie = "".join(str(v) for v in reponse.headers.getlist("Set-Cookie"))
    if cookie:
        valeur = cookie.split("insacloud_session=", 1)[-1].split(";", 1)[0]
        contenu = _decoder_cookie(valeur)
        assert secret not in contenu, "le mot de passe ne doit pas être dans le cookie"

    # …mais il doit bien s'afficher dans la page, une seule fois
    assert secret in reponse.get_data(as_text=True)
    assert secret not in connecte.get("/dashboard").get_data(as_text=True), \
        "le mot de passe ne doit apparaître qu'une fois"


def test_un_marqueur_pour_la_machine_d_autrui_ne_revele_rien(client, monkeypatch):
    """Forger l'identifiant dans son propre cookie ne donne accès à rien."""
    monkeypatch.setattr(insacloud, "docker_container_state", lambda *a, **k: "running")
    inscrire(client, "alice")
    alice = db.get_user_by_username("alice")
    identifiant = db.create_instance(alice["id"], "aaa111222333", "insacloud_alice_c1",
                                     8021, 30, os_type="ubuntu", mode="terminal",
                                     term_port=8121, gui_port=None, worker="local")
    db.set_instance_password(identifiant, crypto.encrypt_secret("SecretDAlice1234"))
    client.post("/logout", data={"_csrf": pose_csrf(client)})

    inscrire(client, "bob")
    with client.session_transaction() as session:
        session["reveal"] = {"instance": identifiant, "rotated": False}
    assert "SecretDAlice1234" not in client.get("/dashboard").get_data(as_text=True)
