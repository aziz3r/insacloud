"""
crypto.py - Secrets de la plateforme.

Deux clés distinctes, jamais la même :
  * la clé de session Flask, qui signe les cookies ;
  * la clé du coffre (Fernet), qui chiffre les mots de passe root en base.

Chacune vient de l'environnement — Ansible les injecte depuis son Vault — ou,
à défaut, d'un fichier local en 0600 créé une seule fois. Séparer les deux
garantit qu'un vol de la clé de session ne donne pas accès aux machines louées.
"""

import logging
import os
import secrets

from cryptography.fernet import Fernet, InvalidToken

from config import BASE_DIR

log = logging.getLogger("insacloud.crypto")


def load_or_create_key(env_name: str, filename: str, generate) -> str:
    """Clé lue dans l'environnement (Ansible/Vault) ou dans un fichier local 0600."""
    key = os.environ.get(env_name)
    if key:
        return key
    path = os.path.join(BASE_DIR, filename)
    try:
        with open(path, encoding="utf-8") as f:
            key = f.read().strip()
            if key:
                return key
    except FileNotFoundError:
        pass
    key = generate()
    with open(path, "w", encoding="utf-8") as f:
        f.write(key)
    os.chmod(path, 0o600)
    log.info("Clé %s générée et enregistrée dans %s", env_name, filename)
    return key


def load_secret_key() -> str:
    """Clé secrète des sessions Flask."""
    return load_or_create_key("INSACLOUD_SECRET_KEY", ".secret_key",
                              lambda: secrets.token_hex(32))


# Clé de chiffrement des mots de passe root (distincte de la clé de session)
VAULT = Fernet(load_or_create_key("INSACLOUD_VAULT_KEY", ".vault_key",
                                  lambda: Fernet.generate_key().decode()))


def encrypt_secret(value: str) -> str:
    """Chiffre un secret avant de l'écrire en base."""
    return VAULT.encrypt(value.encode()).decode()


def decrypt_secret(token: str):
    """Retourne le secret en clair, ou None s'il est absent / illisible (clé changée)."""
    if not token:
        return None
    try:
        return VAULT.decrypt(token.encode()).decode()
    except (InvalidToken, ValueError):
        return None
