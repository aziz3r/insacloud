<h1 align="center">InsaCloud</h1>

<p align="center">
  <strong>Un mini-fournisseur de cloud, construit de zéro.</strong><br>
  Louez une machine Linux à la minute, travaillez dedans depuis votre navigateur,<br>
  et laissez-la se détruire toute seule à l'expiration.
</p>

<p align="center">
  <img src="https://img.shields.io/badge/Python-3.10+-3776AB?logo=python&logoColor=white" alt="Python">
  <img src="https://img.shields.io/badge/Flask-Gunicorn-000000?logo=flask&logoColor=white" alt="Flask">
  <img src="https://img.shields.io/badge/Docker-Engine-2496ED?logo=docker&logoColor=white" alt="Docker">
  <img src="https://img.shields.io/badge/Ansible-8_rôles-EE0000?logo=ansible&logoColor=white" alt="Ansible">
  <img src="https://img.shields.io/badge/Vagrant-3_VM-1868F2?logo=vagrant&logoColor=white" alt="Vagrant">
  <img src="https://img.shields.io/badge/nginx-TLS_1.3-009639?logo=nginx&logoColor=white" alt="nginx">
</p>

---

## La démo en 2 minutes

**▶ [Regarder la vidéo](docs/demo.mp4)** — tout le parcours, enregistré sur l'application réelle : création de compte, location d'une machine Alpine, terminal dans le navigateur, location d'un bureau Debian, XFCE en ligne, prolongation, fin de location.

<table>
<tr>
<td width="50%"><img src="docs/03-tableau-de-bord.png" alt="Tableau de bord"></td>
<td width="50%"><img src="docs/05-mot-de-passe-unique.png" alt="Mot de passe affiché une seule fois"></td>
</tr>
<tr>
<td><b>Louer une machine en trois choix</b><br><sub>Distribution, mode d'accès, durée. Elle est prête en quelques secondes.</sub></td>
<td><b>Le mot de passe root s'affiche une seule fois</b><br><sub>Ensuite il est chiffré ; le revoir demande de ressaisir son propre mot de passe.</sub></td>
</tr>
<tr>
<td><img src="docs/07-terminal-web.png" alt="Terminal web"></td>
<td><img src="docs/09-bureau-applications.png" alt="Bureau XFCE dans le navigateur"></td>
</tr>
<tr>
<td><b>Un terminal, sans rien installer</b><br><sub>Invite de connexion classique, servie en TLS par la machine elle-même.</sub></td>
<td><b>Ou un bureau complet</b><br><sub>XFCE et Firefox diffusés dans le navigateur par noVNC.</sub></td>
</tr>
</table>

## Pourquoi c'est intéressant

Derrière une interface volontairement simple, le projet répond à des questions que se pose n'importe quelle plateforme d'hébergement :

- **Où placer une nouvelle machine ?** Un contrôleur répartit les conteneurs sur le worker le moins chargé et pilote leur Docker à distance, sans jamais exposer d'API Docker sur le réseau.
- **Comment ne pas perdre l'état ?** Les machines survivent au redémarrage du démon Docker *et* de leur hôte, avec les données de l'utilisateur.
- **Comment gérer des secrets qu'on doit pouvoir réafficher ?** Le mot de passe root n'est jamais stocké en clair, et sa consultation exige une re-authentification limitée dans le temps.
- **Comment garantir que le service se relève seul ?** Un watchdog vérifie chaque minute le site, le nettoyeur et le proxy, et redémarre ce qui est tombé.
- **Comment reconstruire toute l'infrastructure à l'identique ?** Trois VM et un playbook idempotent : `vagrant up` puis `ansible-playbook`, et rien d'autre.

## Architecture

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/architecture-dark.svg">
  <img src="docs/architecture.svg" alt="Architecture : un contrôleur, deux workers">
</picture>

Le **contrôleur** sert le site et décide ; il n'exécute aucun conteneur loué et n'a même pas de démon Docker, seulement le client. Il pilote les **workers** par SSH (`docker -H ssh://insacloud@worker1`), avec une clé dédiée et un compte sans mot de passe. Les utilisateurs, eux, joignent directement leur machine sur le worker : SSH, terminal web, bureau.

Sans worker dans l'inventaire, tout retombe automatiquement sur une seule machine — pratique pour développer.

## Le cycle de vie d'une machine

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/cycle-de-vie-dark.svg">
  <img src="docs/cycle-de-vie.svg" alt="Cycle de vie d'une machine louée">
</picture>

## Ce qu'on peut louer

|  | Terminal | Bureau graphique |
|---|---|---|
| **Ubuntu 22.04** | SSH + terminal web | + XFCE et Firefox |
| **Debian 12** | SSH + terminal web | + XFCE et Firefox |
| **Alpine 3.20** | SSH + terminal web | + XFCE et Firefox |
| Mémoire | 256 Mo | 1 Go |
| Taille de l'image | 118 – 303 Mo | 1,3 – 1,5 Go |

Six images, construites depuis trois Dockerfiles : la variante bureau s'active par un simple `--build-arg DESKTOP=1`, et un script d'entrée commun configure les services selon le mode.

<img src="docs/10-machines-en-cours.png" alt="Deux machines en cours, une par mode d'accès">

## Sous le capot

<details>
<summary><b>Haute disponibilité à deux niveaux</b></summary>

Dans chaque machine, `supervisord` tourne en PID 1 et surveille ses services (sshd, terminal web, serveur d'affichage, bureau, noVNC) : si l'un meurt, il le relance. Si supervisord lui-même s'arrête, Docker relance le conteneur grâce à `--restart=always`. Enfin, le démon Docker des workers est en `live-restore` : une mise à jour de Docker ne coupe pas les machines en cours.

Vérifié en conditions réelles : un worker entièrement redémarré retrouve ses machines **et** les fichiers créés dedans.
</details>

<details>
<summary><b>Le « Faucheur » : un démon qui nettoie</b></summary>

Un service séparé lit la base toutes les dix secondes et détruit les machines dont la date de fin est dépassée. La détection tient en une requête SQL — les dates sont stockées au format de `datetime('now')`, ce qui rend la comparaison native.

Il fait aussi de la **réconciliation** : un conteneur présent sur un worker mais inconnu de la base est supprimé, une machine enregistrée dont le conteneur a disparu passe à « arrêtée ». Un worker injoignable est ignoré, jamais interprété comme une disparition.
</details>

<details>
<summary><b>Des secrets qu'on peut revoir sans les stocker en clair</b></summary>

Le mot de passe root est tiré au sort (16 caractères, ~90 bits), montré une fois, puis chiffré avec Fernet — la clé vient du Vault Ansible et ne se trouve jamais dans la base. Pour le réafficher, l'utilisateur ressaisit **son** mot de passe de compte : le coffre s'ouvre cinq minutes, puis se reverrouille.

Un bouton permet aussi de régénérer le mot de passe à chaud : SSH, terminal web et bureau prennent le nouveau immédiatement. Et si l'utilisateur enregistre sa clé SSH publique, ses machines n'acceptent plus que cette clé.

<img src="docs/06-coffre-ouvert.png" alt="Coffre ouvert : accès visibles pendant 5 minutes">
</details>

<details>
<summary><b>Ce que la plateforme refuse de faire</b></summary>

Chaque conteneur démarre avec `--cap-drop ALL` puis les dix capacités strictement nécessaires, `no-new-privileges`, une limite de processus et de CPU. Le serveur d'affichage n'écoute que sur la boucle locale ; terminal web et bureau sont chiffrés avec le certificat du nœud.

Côté site : jetons CSRF sur tous les formulaires, verrouillage après cinq échecs de connexion, politique de mot de passe, cookies `Secure`/`HttpOnly`/`SameSite=Strict`, et une CSP `default-src 'self'` — donc aucun CDN, aucun script en ligne : Tailwind est précompilé et les polices sont auto-hébergées.
</details>

<details>
<summary><b>Une infrastructure reproductible</b></summary>

`Vagrantfile` multi-provider (VMware, Parallels, QEMU pour Apple Silicon, VirtualBox en repli) décrivant les trois VM. Puis huit rôles Ansible : pare-feu, clé de pilotage, Docker, certificats, workers, images, proxy TLS, application.

Le playbook est **idempotent** — second passage : `changed=0` sur les trois nœuds — et un mode démonstration ne construit que l'image la plus légère pour un déploiement en quelques minutes.
</details>

## Essayer en local

Il faut Python 3.10+ et un Docker (Docker Desktop, ou `brew install colima docker && colima start`).

```bash
git clone https://github.com/aziz3r/insacloud.git && cd insacloud

# Les images des machines (la version terminal suffit pour essayer)
cd 1_docker
docker build -t insacloud_alpine:latest -f alpine.Dockerfile .
cd ..

# Le site (crée son environnement Python au premier lancement)
cd 2_webapp && ./run_local.sh
```

Puis <http://localhost:5055> : créez un compte et louez votre première machine.

<details>
<summary>Construire les six images (dont les bureaux graphiques)</summary>

```bash
cd 1_docker
for d in ubuntu:Dockerfile debian:debian.Dockerfile alpine:alpine.Dockerfile; do
  n=${d%%:*}; f=${d##*:}
  docker build -t insacloud_$n:latest         -f $f .
  docker build -t insacloud_${n}_desktop:latest --build-arg DESKTOP=1 -f $f .
done
```
</details>

## Déployer l'infrastructure complète

```bash
vagrant up                                          # controller + worker1 + worker2
cd 3_ansible
ansible-galaxy collection install -r requirements.yml
ansible-playbook site.yml --ask-vault-pass          # déploie les trois nœuds
ansible-playbook site.yml --ask-vault-pass          # à nouveau : changed=0
```

Le site est alors sur **https://192.168.56.10** (certificat auto-signé). Pour construire les six images sur les workers : `-e demo_mode=false`.

<details>
<summary>Ce que fait le playbook, rôle par rôle</summary>

| Rôle | Sur | Ce qu'il installe |
|---|---|---|
| `security_hardening` | tous | UFW : tout refusé sauf SSH (limité), 80/443 sur le contrôleur, 8000-9000 sur les workers |
| `controller_key` | contrôleur | compte de service et clé SSH de pilotage |
| `docker` | tous | Docker Engine avec `live-restore` sur les workers, client seul sur le contrôleur |
| `tls_cert` | tous | certificat ECDSA auto-signé, propre à chaque nœud |
| `worker` | workers | compte `insacloud`, clé du contrôleur, SSH par clé uniquement |
| `docker_images` | workers | images des machines louées |
| `reverse_proxy` | contrôleur | nginx TLS 1.2/1.3, HTTP/2, redirection 80→443, limitation sur `/login` |
| `webapp` | contrôleur | Flask + Gunicorn, Faucheur, watchdog, secrets du Vault |

Les secrets (clé de session, clé de chiffrement) vivent dans un fichier **Ansible Vault** chiffré, et atterrissent sur le serveur dans un fichier `0600` lu par systemd — jamais dans une unité ni dans les journaux.
</details>

## Structure du dépôt

```
1_docker/        3 Dockerfiles + un script d'entrée commun (SSH, terminal web, bureau)
2_webapp/        Flask : location, coffre, ordonnancement, Faucheur, interface
3_ansible/       8 rôles, inventaire à 3 nœuds, Vault
Vagrantfile      controller 192.168.56.10 · worker1 .11 · worker2 .12
docs/            vidéo de démonstration, captures, schémas
```

## Choses apprises en chemin

Quelques problèmes qui ont demandé de creuser, et ce qu'ils ont appris :

- **`docker kill` ne déclenche pas `--restart=always`.** C'est volontaire : Docker distingue un arrêt manuel d'un crash. La bonne démonstration de résilience consiste donc à tuer le processus *à l'intérieur* du conteneur.
- **Une boîte de dialogue d'authentification HTTP n'est pas une interface.** Le terminal web était protégé par `Basic Auth` ; certains navigateurs affichaient une page blanche. Le remplacer par `login` dans le terminal a rendu l'expérience à la fois plus familière et plus sûre (PAM, temporisation, expiration).
- **Un mot de passe VNC fait huit caractères maximum.** Contrainte du protocole, pas un choix : d'où un mot de passe de bureau distinct, affiché comme tel, et un transport chiffré par-dessus.
- **Firefox n'existe qu'en Snap sur Ubuntu**, inutilisable dans un conteneur — il a fallu passer par le dépôt officiel de Mozilla. Et `ttyd` n'est pas empaqueté dans Debian 12 : binaire statique officiel.
- **Un venv est obligatoire depuis PEP 668**, ce qui change la façon d'écrire un rôle Ansible qui installe des dépendances Python.

## Limites assumées

- Certificat auto-signé par défaut (remplaçable par `mkcert` ou une autorité interne).
- Docker publie ses ports directement dans iptables : un filtrage strict des conteneurs passerait par la chaîne `DOCKER-USER`.
- Pas d'authentification à deux facteurs ni de quotas par ressource — la suite logique.

---

<sub>Réalisé seul dans le cadre du cours *Outils de déploiement de plateformes* (INSA, STI 4A), un sujet prévu pour un groupe de quatre à cinq personnes.</sub>
