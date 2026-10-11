# Vérifier la sauvegarde du travail

Quand une machine est détruite (à l'échéance, ou avec le bouton **Terminer**), son
travail (`/root` et `/home`) est sauvegardé dans une archive que l'utilisateur
télécharge depuis son tableau de bord pendant sept jours. Ce document explique
comment le vérifier soi-même, de deux façons.

> **À savoir** : l'archivage est **désactivé par défaut** dans le code. Il faut
> lancer le site avec `INSACLOUD_ARCHIVE_ENABLED=1` (c'est déjà fait dans
> `docker-compose.yml` et dans Ansible). Sans cette variable, la section
> « Archives de mon travail » n'apparaît jamais.

## 1. Sans Docker : les tests automatiques (une minute)

Docker y est simulé. Les tests vérifient notamment que l'archive est faite
**avant** le `docker rm -f`, qu'un archivage raté n'empêche pas la destruction,
qu'un utilisateur ne peut pas télécharger l'archive d'un autre, et qu'un chemin
piégé (`../`) ne sort jamais du dossier des archives.

Linux / macOS :

```bash
cd 2_webapp
python3 -m venv .venv && .venv/bin/pip install -q -r requirements-dev.txt
.venv/bin/pytest -q tests/test_archives.py tests/test_archives_base.py tests/test_archivage_destruction.py tests/test_archives_web.py tests/test_archives_purge.py tests/test_archives_api.py
```

Windows (PowerShell) :

```powershell
cd 2_webapp
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements-dev.txt
python -m pytest -q tests/test_archives.py tests/test_archives_base.py tests/test_archivage_destruction.py tests/test_archives_web.py tests/test_archives_purge.py tests/test_archives_api.py
```

Résultat attendu : `49 passed` (sous Windows, `48 passed, 1 skipped` : le test des
droits de fichier Unix est ignoré). La suite complète (`pytest -q`) compte 143 tests.

## 2. Avec de vrais conteneurs (cinq minutes)

Prérequis : Docker démarré (`docker version` doit afficher un bloc `Server:`) et
Python 3.10 ou plus.

### a. Construire une image louable

Depuis la racine du projet (même commande sous Linux, macOS et Windows) :

```bash
docker build -t insacloud_alpine:latest -f 1_docker/alpine.Dockerfile 1_docker
```

### b. Lancer le site avec l'archivage activé (laisser ce terminal ouvert)

Linux / macOS :

```bash
cd 2_webapp
python3 -m venv .venv && .venv/bin/pip install -q -r requirements.txt
INSACLOUD_PORT=5055 INSACLOUD_EMBED_FAUCHEUR=1 INSACLOUD_FAUCHEUR_INTERVAL=5 \
INSACLOUD_ARCHIVE_ENABLED=1 .venv/bin/python app.py
```

Windows (PowerShell) :

```powershell
cd 2_webapp
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
$env:INSACLOUD_PORT="5055"; $env:INSACLOUD_EMBED_FAUCHEUR="1"; $env:INSACLOUD_FAUCHEUR_INTERVAL="5"; $env:INSACLOUD_ARCHIVE_ENABLED="1"
python app.py
```

### c. Louer une machine

Ouvrir <http://127.0.0.1:5055>, créer un compte (mot de passe long, par exemple
`Tr0ub4dour-Insa!`), puis louer une machine : **Alpine**, mode **Terminal**,
**30 minutes**.

### d. Écrire un mot dans un fichier de la machine

Dans un **second terminal** (la variable `NOM` n'existe que dans ce terminal) :

Linux / macOS :

```bash
NOM=$(docker ps --format '{{.Names}}' | head -1)
docker exec "$NOM" sh -c "echo bonjour-archive > /root/mon-travail.txt"
docker exec "$NOM" cat /root/mon-travail.txt
```

Windows (PowerShell) :

```powershell
$NOM = docker ps --format "{{.Names}}" | Select-Object -First 1
docker exec $NOM sh -c "echo bonjour-archive > /root/mon-travail.txt"
docker exec $NOM cat /root/mon-travail.txt
```

La dernière commande affiche `bonjour-archive`.

### e. Détruire la machine

Sur le tableau de bord, cliquer sur le bouton rouge **Terminer** et confirmer. La
machine disparaît, et son fichier avec elle : `docker ps` ne la liste plus.

### f. Retrouver le mot

Sur le tableau de bord, la section **« Archives de mon travail »** liste la
machine. Cliquer sur **Télécharger**, puis, dans le second terminal :

Linux / macOS :

```bash
tar -xzOf ~/Downloads/"$NOM".tar.gz root/mon-travail.txt
```

Windows (PowerShell) :

```powershell
tar -xzOf "$HOME\Downloads\$NOM.tar.gz" root/mon-travail.txt
```

La commande affiche `bonjour-archive` : le fichier a survécu à la destruction de
la machine. Si le navigateur a renommé le téléchargement (par exemple avec un
`(1)`), utiliser le nom exact du fichier.

Sans navigateur, on peut aussi lire les archives directement sur le disque du
serveur, depuis `2_webapp` :

```bash
for f in archives/*/*.tar.gz; do echo "--- $f"; tar -xzOf "$f" root/mon-travail.txt; done
```

```powershell
Get-ChildItem -Recurse archives -Filter *.tar.gz | ForEach-Object { "--- " + $_.FullName; tar -xzOf $_.FullName root/mon-travail.txt }
```

### g. Variante : l'expiration automatique

Louer une machine pour **5 minutes**, y écrire le fichier (étape d), puis attendre
sans rien faire. À l'échéance, le Faucheur archive la machine puis la détruit :
l'archive apparaît toute seule dans le tableau de bord.

### h. Nettoyer

`Ctrl+C` dans le terminal du site, puis supprimer les machines restantes et les
fichiers du test (`2_webapp/archives`, `2_webapp/insacloud.db`, déjà exclus de Git).

```bash
docker ps -aq --filter "name=insacloud_" | xargs -r docker rm -f
```

```powershell
docker ps -aq --filter "name=insacloud_" | ForEach-Object { docker rm -f $_ }
```