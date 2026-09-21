# InsaCloud ☁️

> Mini-fournisseur de cloud : louez une machine Linux (Ubuntu, Debian ou Alpine) pour quelques minutes,
> en mode **terminal** ou avec un **bureau graphique dans le navigateur**, et laissez-la disparaître toute seule.

Projet 3 du cours *Outils de déploiement de plateformes* — INSA, STI 4A (Dr. Nadia Fettah).

**Stack** : Python / Flask + Gunicorn · nginx (TLS) · SQLite · Docker · Ansible (rôles + Vault) · Vagrant · UFW · Tailwind CSS (précompilé) · systemd

---

## Sommaire

1. [Fonctionnalités](#fonctionnalités)
2. [Architecture](#architecture)
3. [Arborescence](#arborescence)
4. [Démarrage rapide (poste de développement)](#démarrage-rapide-poste-de-développement)
5. [Déploiement de production (Vagrant + Ansible)](#déploiement-de-production-vagrant--ansible)
6. [Utilisation](#utilisation)
7. [Fonctionnement interne](#fonctionnement-interne)
8. [Configuration](#configuration)
9. [Sécurité](#sécurité)
10. [Démonstrations pour la soutenance](#démonstrations-pour-la-soutenance)
11. [Problèmes rencontrés et solutions](#problèmes-rencontrés-et-solutions)
12. [Limites et pistes d'amélioration](#limites-et-pistes-damélioration)

---

## Fonctionnalités

| | |
|---|---|
| **Comptes** | inscription / connexion, mots de passe hachés (Werkzeug), sessions signées |
| **3 distributions** | Ubuntu 22.04 · Debian 12 · Alpine Linux 3.20 |
| **2 modes** | **Terminal** (SSH + terminal web `ttyd`) ou **Bureau graphique** (XFCE + Firefox via noVNC, + SSH) |
| **Location à durée limitée** | 1 à 120 min, prolongeable, quota de 3 machines par utilisateur |
| **Accès** | commande SSH exacte (`ssh root@<hôte> -p <port>`), bouton *Terminal*, bouton *Bureau*, clé SSH personnelle ou mot de passe root **affiché une seule fois** |
| **Sécurité** | mot de passe jamais stocké et rotatif, CSRF, anti-force-brute, CSP stricte sans CDN, HTTPS de bout en bout, conteneurs à capacités minimales — voir [Sécurité](#sécurité) |
| **Haute disponibilité** | `--restart=always` + `supervisord` dans chaque machine : tout service qui plante est relancé |
| **Le Faucheur** | démon qui détruit (`docker rm -f`) les machines expirées et réconcilie Docker ↔ base |
| **IaC** | VM Vagrant multi-provider, playbook Ansible idempotent en 3 rôles (`security_hardening`, `docker`, `webapp`), secrets chiffrés avec Ansible Vault, Gunicorn sous systemd, pare-feu UFW |
| **Interface** | thème « Liquid Glass » (verre translucide), mode sombre automatique, sans framework JS |

## Architecture

```
            ┌─────────────────────── Poste de contrôle ───────────────────────┐
            │  Vagrant ──► VM Ubuntu 192.168.56.10      Ansible (rôles + Vault)│
            └──────────────────────────────┬───────────────────────────────────┘
                                           │ SSH + sudo
┌──────────────────────────────────────────▼──────────────────────────────────────────┐
│ VM de production                                                                    │
│  systemd ─ insacloud-web ──► Flask (app.py) ─ subprocess ─► docker CLI ─► dockerd   │
│         └ insacloud-faucheur ──► faucheur.py ─┘                 │                   │
│                     └────── SQLite (WAL) ─────┘        conteneurs insacloud_<user>_… │
│  /etc/insacloud/insacloud.env  (SECRET_KEY issue du Vault, 0600)   ports 8000-9000  │
└─────────────────────────────────────────────────────────────────────────────────────┘
        ▲ HTTP :5000                 ▲ ssh -p <port>    ▲ http://…:<port>  (ttyd / noVNC)
   Navigateur ───────────────────────┴───────────────────┘
```

Chaque machine louée est un conteneur Docker qui publie :

| Port interne | Service | Mode |
|---|---|---|
| 22 | OpenSSH (root, mot de passe) | terminal + bureau |
| 7681 | `ttyd` — terminal web (auth HTTP Basic `root`) | terminal + bureau |
| 6080 | noVNC (websockify → Xvnc :1 → XFCE) | bureau uniquement |

## Arborescence

```
Projet_InsaCloud/
├── Vagrantfile                      VM Ubuntu 22.04 en 192.168.56.10 (VMware / Parallels / QEMU / VirtualBox)
├── 1_docker/
│   ├── Dockerfile                   Ubuntu 22.04   (--build-arg DESKTOP=1 → variante bureau)
│   ├── debian.Dockerfile            Debian 12
│   ├── alpine.Dockerfile            Alpine 3.20
│   └── entrypoint.sh                script commun : mot de passe, supervisord, sshd/ttyd/Xvnc/XFCE/noVNC
├── 2_webapp/
│   ├── app.py                       serveur Flask, routes, sécurité, appels docker via subprocess
│   ├── database.py                  schéma SQLite (users, instances, login_attempts), migrations
│   ├── faucheur.py                  démon de destruction des machines expirées
│   ├── static/                      CSS (Tailwind précompilé + thème) et JS servis localement
│   └── templates/
│       ├── login.html               connexion / inscription
│       └── dashboard.html           location, cartes machines, clé SSH, historique
└── 3_ansible/
    ├── ansible.cfg
    ├── requirements.yml             collections (community.general pour UFW)
    ├── inventaire.ini               vm-insacloud → 192.168.56.10
    ├── site.yml                     compose les rôles docker + webapp
    ├── group_vars/insacloud/
    │   ├── vars.yml                 variables en clair
    │   └── vault.yml                secrets chiffrés (ansible-vault)
    └── roles/
        ├── security_hardening/      pare-feu UFW : SSH limité, 80/443, 8000-9000, deny par défaut
        ├── docker/                  installe Docker Engine (dépôt officiel, amd64/arm64)
        ├── reverse_proxy/           nginx : TLS 1.2/1.3, certificat, rate-limit /login
        └── webapp/                  Python/Flask/Gunicorn (127.0.0.1), code, images, systemd, secrets
```

## Démarrage rapide (poste de développement)

Pré-requis : Python ≥ 3.10, Docker (Docker Desktop, ou `brew install colima docker && colima start` sur macOS).

```bash
git clone https://github.com/aziz3r/insacloud.git
cd insacloud
```

Construire les images des machines louées (les variantes *bureau* pèsent ~1,4 Go et prennent quelques minutes) :

```bash
cd 1_docker
docker build -t insacloud_ubuntu:latest         -f Dockerfile        .
docker build -t insacloud_ubuntu_desktop:latest -f Dockerfile        --build-arg DESKTOP=1 .
docker build -t insacloud_debian:latest         -f debian.Dockerfile .
docker build -t insacloud_debian_desktop:latest -f debian.Dockerfile --build-arg DESKTOP=1 .
docker build -t insacloud_alpine:latest         -f alpine.Dockerfile .
docker build -t insacloud_alpine_desktop:latest -f alpine.Dockerfile --build-arg DESKTOP=1 .
cd ..
```

Lancer l'application (Flask + Faucheur dans le même processus) :

```bash
cd 2_webapp
python3 -m venv .venv && .venv/bin/pip install flask
INSACLOUD_EMBED_FAUCHEUR=1 INSACLOUD_PORT=5055 .venv/bin/python app.py
```

Ouvrez <http://localhost:5055>, créez un compte, louez une machine.

> macOS : le port 5000 est occupé par *AirPlay Receiver* (il répond 403), d'où `INSACLOUD_PORT=5055`.

## Déploiement de production (Vagrant + Ansible)

Pré-requis sur le poste de contrôle : Vagrant, un hyperviseur, Ansible ≥ 2.12.

Le `Vagrantfile` est **multi-provider** — Vagrant prend le premier utilisable dans cet ordre :

| Fournisseur | Plateforme | Installation |
|---|---|---|
| `vmware_desktop` | Mac Apple Silicon / Intel, Windows, Linux | `vagrant plugin install vagrant-vmware-desktop` + Vagrant VMware Utility |
| `parallels` | Mac | `vagrant plugin install vagrant-parallels` |
| `qemu` | Mac Apple Silicon sans hyperviseur commercial | `brew install qemu && vagrant plugin install vagrant-qemu` (ports redirigés sur localhost, pas d'IP 192.168.56.10) |
| `virtualbox` | PC Windows / Linux, Mac Intel (repli) | VirtualBox |

```bash
# 1. Créer la VM (Ubuntu 22.04, 192.168.56.10, 4 Go)
vagrant up                      # ou : vagrant up --provider=vmware_desktop

# 2. Installer la collection Ansible requise (module ufw)
cd 3_ansible
ansible-galaxy collection install -r requirements.yml

# 3. Déployer (le mot de passe du Vault est demandé)
ansible-playbook site.yml --ask-vault-pass

# 4. Relancer pour constater l'idempotence : changed=0
ansible-playbook site.yml --ask-vault-pass
```

Le site est alors sur **<https://192.168.56.10>** (nginx en TLS devant Gunicorn, 4 workers, confiné à `127.0.0.1`). Le certificat est auto-signé : acceptez l'avertissement du navigateur, ou générez-en un de confiance avec `mkcert` et remplacez `/etc/insacloud/tls/{cert,key}.pem`.

**Mode démonstration** : par défaut `demo_mode: true` (`group_vars/insacloud/vars.yml`) — seule l'image légère `insacloud_alpine` est construite (~1 min) et l'interface ne propose que ce choix. Pour les six images (les variantes bureau pèsent ~1,4 Go, 15 à 25 min) :

```bash
ansible-playbook site.yml --ask-vault-pass -e demo_mode=false
```

Le playbook :

| Rôle | Tâches |
|---|---|
| `security_hardening` | installe UFW, politique `deny` entrant / `allow` sortant, SSH 22 en `limit` (anti-force-brute), 5000 (Gunicorn), plage 8000:9000 (machines louées), activation en dernier. Passe **avant** Docker car `ufw enable` recharge iptables |
| `docker` | cache APT, pré-requis, clé GPG et dépôt officiel Docker (architecture détectée), `docker-ce`, service activé, `docker info` |
| `reverse_proxy` | nginx + openssl, certificat ECDSA auto-signé (généré une fois, partagé avec les machines louées), site TLS 1.2/1.3 + HTTP/2, redirection 80→443, `limit_req` sur `/login`, `server_tokens off` |
| `webapp` | Python 3 / pip / venv + Flask + **Gunicorn**, utilisateur système `insacloud` (groupe docker), copie du code et de `1_docker/`, fichier d'environnement secret (0600), unités systemd `insacloud-web` (Gunicorn) et `insacloud-faucheur`, build des images **seulement si absentes ou si les sources ont changé** (filtré par `demo_mode`), démarrage, vérification HTTP 200 |

Commandes utiles sur la VM :

```bash
vagrant ssh
sudo systemctl status insacloud-web insacloud-faucheur
sudo journalctl -u insacloud-faucheur -f
docker ps --filter name=insacloud_
```

### Secrets (Ansible Vault)

La clé secrète des sessions Flask vit dans `3_ansible/group_vars/insacloud/vault.yml`, chiffré en AES-256.

```bash
cd 3_ansible
ansible-vault view  group_vars/insacloud/vault.yml     # lire
ansible-vault edit  group_vars/insacloud/vault.yml     # modifier
ansible-vault rekey group_vars/insacloud/vault.yml     # changer le mot de passe du Vault
```

Pour repartir de zéro avec votre propre secret :

```bash
python3 -c "import secrets; print('vault_insacloud_secret_key: \"' + secrets.token_hex(32) + '\"')" > group_vars/insacloud/vault.yml
ansible-vault encrypt group_vars/insacloud/vault.yml
```

Chaîne d'injection : `vault.yml` → `vars.yml` (`insacloud_secret_key: "{{ vault_insacloud_secret_key }}"`) → template `insacloud.env.j2` → `/etc/insacloud/insacloud.env` (0600 root, tâche `no_log`) → `EnvironmentFile=` des unités systemd → variable `INSACLOUD_SECRET_KEY` lue par Flask.

## Utilisation

1. **Créer un compte** puis se connecter.
2. **Louer** : choisir une distribution, un mode (*Terminal* ou *Bureau graphique*), une durée, puis *Louer cette machine*.
3. Le **mot de passe root s'affiche une seule fois** (bandeau orange) : notez-le, il n'est stocké nulle part. Perdu ? *Régénérer le mot de passe* en crée un nouveau à chaud.
4. Sur la carte de la machine :
   - **Terminal** — ouvre un terminal dans le navigateur (identifiant `root` + mot de passe) ;
   - **Bureau** — ouvre le bureau XFCE dans le navigateur (mot de passe = **8 premiers caractères**, limite du protocole VNC) ;
   - **SSH** — copier la commande, par ex. `ssh root@192.168.56.10 -p 8412`.
5. Recommandé : enregistrez votre **clé publique SSH** (section « Clé SSH publique ») — vos machines n'accepteront alors que cette clé en SSH, l'authentification par mot de passe y est désactivée.
6. **Prolonger** ou **Terminer** à tout moment ; à l'expiration, le Faucheur détruit la machine et elle passe dans l'historique.

## Fonctionnement interne

**Location** (`app.py`, route `POST /instances/create`) :
1. validation (durée 1–120, distribution/mode en liste blanche, quota) ;
2. tirage de 2 ou 3 ports aléatoires dans 8000–9000, chacun vérifié non réservé en base **et** libre sur l'hôte (`socket.bind`) ;
3. `docker run -d --restart=always -p <p1>:22 -p <p2>:7681 [-p <p3>:6080 --shm-size 512m] --name insacloud_<user>_<id> -e ROOT_PASSWORD=<aléatoire> [-e SSH_PUBKEY=…] -v /etc/insacloud/tls:/tls:ro --cap-drop ALL --cap-add <10 capacités> --security-opt no-new-privileges:true --pids-limit 512 --cpus 1 --memory 256m|1g insacloud_<distro>[_desktop]:latest` ;
4. enregistrement en base ; en cas d'échec le conteneur est détruit (aucun orphelin).

**Base SQLite** (`database.py`) : dates en UTC au format de `datetime('now')` → l'expiration se détecte en pur SQL ; `PRAGMA journal_mode=WAL` pour que Flask et le Faucheur partagent le fichier ; migrations douces (`ALTER TABLE … ADD COLUMN`) pour conserver les anciennes bases.

**Le Faucheur** (`faucheur.py`) : toutes les 10 s, `SELECT … WHERE status='running' AND expires_at <= datetime('now')` → `docker rm -f` → `status='expired'`. Toutes les ~60 s, réconciliation : conteneurs `insacloud_*` inconnus de la base supprimés, instances sans conteneur marquées `stopped`. Arrêt propre sur `SIGTERM`.

**Images** (`1_docker/`) : un Dockerfile par distribution, la variante bureau s'active par `--build-arg DESKTOP=1` (`ENV INSACLOUD_MODE=${DESKTOP:+desktop}`). `entrypoint.sh` applique le mot de passe root (et la clé SSH éventuelle, qui désactive alors l'authentification par mot de passe), génère la configuration `supervisord` selon le mode — avec TLS pour `ttyd` et noVNC si `/tls` est monté — puis lance `supervisord` en PID 1 et efface le secret de son environnement. `entrypoint.sh setpass <mdp>` effectue la rotation à chaud (SSH, terminal web, VNC).

## Configuration

Toutes les options sont des variables d'environnement (définies par Ansible dans `/etc/insacloud/insacloud.env`) :

| Variable | Défaut | Rôle |
|---|---|---|
| `INSACLOUD_HOST` / `INSACLOUD_PORT` | `0.0.0.0` / `5000` | écoute du serveur web |
| `INSACLOUD_SECRET_KEY` | générée dans `.secret_key` | clé des sessions Flask |
| `INSACLOUD_DB` | `2_webapp/insacloud.db` | fichier SQLite |
| `INSACLOUD_PORT_MIN` / `INSACLOUD_PORT_MAX` | `8000` / `9000` | plage des ports publiés |
| `INSACLOUD_MAX_INSTANCES` | `3` | quota par utilisateur |
| `INSACLOUD_MAX_DURATION` | `120` | durée maximale (minutes) |
| `INSACLOUD_CONTAINER_MEMORY` / `INSACLOUD_GUI_MEMORY` | `256m` / `1g` | mémoire des machines |
| `INSACLOUD_IMAGE_<DISTRO>[_DESKTOP]` | `insacloud_<distro>[_desktop]:latest` | noms des images |
| `INSACLOUD_AVAILABLE_IMAGES` | vide (tout) | images réellement construites ; l'interface masque les autres (mode démo) |
| `INSACLOUD_HTTPS` / `INSACLOUD_BEHIND_PROXY` | `0` / `0` | site servi en TLS derrière nginx : cookies `Secure`, HSTS, en-têtes `X-Forwarded-*` |
| `INSACLOUD_TLS_DIR` | vide | certificat monté dans les machines : terminal web et noVNC en HTTPS |
| `INSACLOUD_LOGIN_MAX_USER` / `INSACLOUD_LOGIN_MAX_IP` / `INSACLOUD_LOGIN_WINDOW` | `5` / `20` / `15` | verrouillage des connexions (échecs par compte, par IP, fenêtre en minutes) |
| `INSACLOUD_PASSWORD_MIN` | `10` | longueur minimale des mots de passe des comptes |
| `INSACLOUD_CONTAINER_CPUS` / `INSACLOUD_CONTAINER_PIDS` | `1` / `512` | limites CPU et processus par machine |
| `INSACLOUD_SSH_HOST` | hôte de l'URL | hôte affiché dans la commande SSH |
| `INSACLOUD_FAUCHEUR_INTERVAL` | `10` | période du Faucheur (s) |
| `INSACLOUD_EMBED_FAUCHEUR` | `0` | `1` = Faucheur en thread dans Flask (dev) |

## Sécurité

Modèle de menace : utilisateurs authentifiés mais non fiables, réseau local hostile, machines louées potentiellement compromises par leur locataire.

| Domaine | Mesure |
|---|---|
| **Secrets des machines** | Mot de passe root de 16 caractères (CSPRNG), **affiché une seule fois** puis jamais stocké (la colonne en base reste vide) ; **rotation à chaud** ; effacé de l'environnement du PID 1 ; clé publique SSH par utilisateur → SSH **par clé uniquement** (`PasswordAuthentication no`) |
| **Comptes** | PBKDF2-SHA256 salé (Werkzeug), comparaison en temps constant même si le compte n'existe pas ; mot de passe ≥ 10 caractères, ni courant ni égal à l'identifiant ; **verrouillage** après 5 échecs / 15 min par compte et 20 par IP, journalisé avec l'adresse IP |
| **Sessions** | cookie `HttpOnly`, `Secure` (en HTTPS), `SameSite=Strict`, durée 2 h, session recréée à la connexion (anti-fixation), déconnexion en POST |
| **CSRF** | jeton par session sur **tous** les formulaires, vérifié en temps constant sur toute requête modifiante |
| **En-têtes** | CSP stricte `default-src 'self'` (aucun CDN, aucun script ni style inline — Tailwind précompilé), `X-Frame-Options: DENY`, `nosniff`, `Referrer-Policy: no-referrer`, `Permissions-Policy`, COOP/CORP, `Cache-Control: no-store` sur les pages |
| **Transport** | nginx TLS 1.2/1.3 + HTTP/2, HSTS, redirection 80→443, `limit_req` sur `/login`, `server_tokens off` ; Gunicorn confiné à `127.0.0.1` ; terminal web (`ttyd --ssl`) et noVNC (`websockify --cert`) chiffrés avec le même certificat |
| **Conteneurs** | `--cap-drop ALL` + 10 capacités nécessaires à sshd, `--security-opt no-new-privileges`, `--pids-limit 512`, `--cpus 1`, `--memory`, Xvnc lié à `localhost`, terminal web protégé par authentification |
| **Entrées** | listes blanches (distribution, mode, durée, clé SSH par expression régulière), requêtes SQL paramétrées, `MAX_CONTENT_LENGTH` 16 Ko, échappement Jinja2 |
| **Isolation** | chaque machine n'est manipulable que par son propriétaire (`user_id` vérifié à chaque action) |
| **Serveur** | UFW (deny par défaut, SSH en `limit`, 80/443, 8000-9000), secrets Ansible chiffrés (Vault) et déposés en 0600, services systemd sous un utilisateur sans shell |

Limites connues : le mot de passe VNC est tronqué à 8 caractères par le protocole ; le certificat est auto-signé (remplacez-le par un certificat de confiance) ; Docker publie ses ports directement dans iptables (filtrage strict possible via `DOCKER-USER`).

## Démonstrations pour la soutenance

**Haute disponibilité** — faire mourir le service principal *de l'intérieur* (Docker ignore volontairement `--restart=always` après un `docker stop`/`docker kill` manuel) :

```bash
docker exec insacloud_<user>_<id> kill -TERM 1
docker inspect -f '{{.State.Status}} redémarrages={{.RestartCount}}' insacloud_<user>_<id>
```

**Le Faucheur** — louer une machine pour 1 minute puis suivre `journalctl -u insacloud-faucheur -f` (ou la console en dev).

**Idempotence** — relancer `ansible-playbook site.yml --ask-vault-pass` : `changed=0`.

**Secret jamais en clair** — sur la VM : `sudo cat /etc/insacloud/insacloud.env` (0600) vs `cat /etc/systemd/system/insacloud-web.service` (aucun secret).

## Problèmes rencontrés et solutions

| Problème | Solution |
|---|---|
| `docker kill` ne relance pas le conteneur | politique de redémarrage ignorée après un arrêt manuel → démonstration par un vrai crash (`kill -TERM 1` depuis l'intérieur) |
| `kill -9 1` sans effet dans un conteneur | le noyau protège le PID 1 des signaux non gérés → SIGTERM |
| Firefox indisponible sur Ubuntu 22.04 (Snap) | dépôt APT officiel de Mozilla (`firefox-esr`, amd64 + arm64) |
| `ttyd` absent de Debian 12 | binaire statique officiel, architecture détectée au build |
| `ttyd` en lecture seule (≥ 1.7) | détection de l'option `-W` à l'exécution |
| `vncpasswd` introuvable sur Debian 12 | paquet `tigervnc-tools`, binaire détecté à l'exécution |
| Mot de passe VNC limité à 8 caractères | mot de passe bureau = 8 premiers caractères, affiché sur la carte |
| Flask et Faucheur sur la même base SQLite | mode WAL |
| `pip install` interdit sur Ubuntu récent (PEP 668) | virtualenv géré par Ansible |
| Port 5000 pris par AirPlay sur macOS | port configurable |

## Développement

Après modification des templates, recompiler la feuille Tailwind (binaire autonome, sans Node) :

```bash
cd 2_webapp && tailwindcss -i static/css/tailwind.src.css -o static/css/tailwind.css --content "./templates/*.html" --minify
```

## Limites et pistes d'amélioration

- Certificat auto-signé par défaut (utiliser `mkcert` ou une autorité interne).
- Docker publie ses ports directement dans iptables : pour un filtrage strict des conteneurs, utiliser la chaîne `DOCKER-USER`.
- Authentification à deux facteurs (TOTP) et journal d'audit exportable.
- Images bureau volumineuses (~1,4 Go) → registre Docker privé pour éviter de reconstruire sur chaque serveur.
- Un seul nœud Docker → orchestrateur (Swarm / Kubernetes) pour plusieurs hôtes.

---

Auteur : Aziz Baoueb — INSA, STI 4A.
