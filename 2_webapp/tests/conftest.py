"""
conftest.py - Socle commun des tests InsaCloud.

L'application lit sa configuration dans l'environnement **au moment de
l'import** (chemin de la base, clés de chiffrement). Ce fichier est chargé par
pytest avant tout module de test : l'environnement y est donc préparé avant
d'importer `app`, ce qui garantit que les tests n'écrivent jamais dans la base
ni dans les clés du poste de développement.
"""

import os
import sys
import tempfile

from cryptography.fernet import Fernet

WEBAPP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, WEBAPP_DIR)

# --- Environnement de test, fixé AVANT l'import de l'application -------------
_TMP = tempfile.mkdtemp(prefix="insacloud-tests-")
os.environ["INSACLOUD_DB"] = os.path.join(_TMP, "test.db")
os.environ["INSACLOUD_SECRET_KEY"] = "cle-de-session-de-test-uniquement"
os.environ["INSACLOUD_VAULT_KEY"] = Fernet.generate_key().decode()
os.environ["INSACLOUD_WORKERS"] = ""          # mode mono-hôte
os.environ["INSACLOUD_HTTPS"] = "0"
os.environ.pop("INSACLOUD_AVAILABLE_IMAGES", None)   # toutes les images réputées présentes

import pytest                                  # noqa: E402

import app as insacloud                        # noqa: E402
import database as db                          # noqa: E402

CSRF = "jeton-csrf-de-test"
GOOD_PASSWORD = "Tr0ub4dour-Insa!"


@pytest.fixture(autouse=True)
def base_vierge():
    """Chaque test part d'une base vide : aucun test n'hérite de l'état d'un autre."""
    try:
        os.remove(db.DB_PATH)
    except FileNotFoundError:
        pass
    db.init_db()
    yield


@pytest.fixture
def client():
    insacloud.app.config["TESTING"] = True
    with insacloud.app.test_client() as c:
        yield c


def pose_csrf(client) -> str:
    """Installe un jeton CSRF connu dans la session et le retourne."""
    with client.session_transaction() as session:
        session["_csrf"] = CSRF
    return CSRF


def inscrire(client, username="etudiant", password=GOOD_PASSWORD):
    """Crée un compte et laisse le client connecté."""
    pose_csrf(client)
    return client.post("/register", data={
        "_csrf": CSRF, "username": username,
        "password": password, "confirm": password,
    }, follow_redirects=True)


@pytest.fixture
def connecte(client):
    """Client déjà inscrit et connecté, avec un jeton CSRF utilisable."""
    inscrire(client)
    pose_csrf(client)
    return client
