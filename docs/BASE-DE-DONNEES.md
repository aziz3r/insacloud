# Base de données InsaCloud

Six tables, décrites en SQLAlchemy dans [`2_webapp/models.py`](../2_webapp/models.py)
et versionnées par Alembic dans [`2_webapp/migrations/`](../2_webapp/migrations/).

Le moteur est SQLite par défaut — un fichier, aucun service à installer — ou
PostgreSQL via `DATABASE_URL`, ce que fait la pile `docker compose`. Le code ne
change pas : c'est l'ORM qui absorbe la différence.

---

## Le choix structurant : instance ≠ location

`Instance` décrit un objet **technique** — un conteneur, sur un nœud, avec ses
ports. `Rental` décrit un engagement **commercial** — un utilisateur, de telle
date à telle date.

Les fusionner aurait été plus court, mais aurait rendu impossible la reprise sur
panne : quand un worker tombe, la machine est recréée ailleurs, avec un nouveau
conteneur, un nouveau port et un nouveau mot de passe. C'est l'`Instance` qui
change ; la `Rental` ne bouge pas, et l'utilisateur garde la durée qu'il a
réservée. Le compteur `migrations` mémorise combien de fois cela s'est produit.

---

## Schéma

```
   users                    rentals                   instances
┌──────────────┐        ┌───────────────┐        ┌──────────────────┐
│ id        PK │───┐    │ id         PK │   ┌────│ id            PK │
│ username  UQ │   └───<│ user_id    FK │   │    │ container_id     │
│ email     UQ │        │ instance_id FK│>──┘    │ container_name   │
│ password_hash│        │ start_time    │        │ worker_id     FK │>──┐
│ ssh_public_key│       │ end_time      │        │ distribution_id FK│>┐│
│ created_at   │        │ status        │        │ ssh_port         │ ││
└──────────────┘        └───────────────┘        │ mode             │ ││
                                                 │ term_port        │ ││
   login_attempts                                │ gui_port         │ ││
┌──────────────┐                                 │ root_password ⚿  │ ││
│ id        PK │                                 │ status           │ ││
│ username     │                                 │ migrations       │ ││
│ ip           │                                 │ created_at       │ ││
│ success      │                                 │ terminated_at    │ ││
│ created_at   │                                 └──────────────────┘ ││
└──────────────┘                                                      ││
                          distributions                     workers   ││
                        ┌──────────────────┐        ┌─────────────────┘│
                        │ id            PK │<───────│ id           PK  │<┘
                        │ name          UQ │        │ hostname     UQ  │
                        │ docker_image     │        │ ip               │
                        │ version          │        │ status           │
                        │ status           │        │ cpu / memory     │
                        │ label / hint     │        │ capacity         │
                        └──────────────────┘        │ last_heartbeat   │
                                                    │ registered_at    │
   ⚿ chiffré (Fernet)                               └──────────────────┘
```

---

## Les tables

### `users`

| Colonne | Type | Notes |
|---|---|---|
| `id` | entier | clé primaire |
| `username` | 32 car. | unique ; `[A-Za-z0-9._-]`, compatible avec les noms Docker |
| `email` | 255 car. | unique, facultatif |
| `password_hash` | texte | `scrypt` via Werkzeug — jamais le mot de passe |
| `ssh_public_key` | texte | si renseignée, les machines n'acceptent plus que cette clé |
| `created_at` | date | UTC |

### `distributions`

Le catalogue vivait dans un dictionnaire de `app.py` ; il est en base pour
pouvoir désactiver une distribution (`status = DISABLED`) sans redéployer.

| Colonne | Exemple |
|---|---|
| `name` | `alpine` |
| `docker_image` | `insacloud_alpine` |
| `version` | `3.20` |
| `status` | `ACTIVE` / `DISABLED` |
| `label`, `hint` | libellé et accroche affichés sur le tableau de bord |

### `workers`

Alimentée par `POST /workers/register` : aucun nœud n'est écrit à la main.

| Colonne | Notes |
|---|---|
| `hostname` | unique ; nom annoncé par l'agent |
| `ip` | adresse que le contrôleur utilise pour joindre le nœud |
| `status` | `AVAILABLE` · `BUSY` · `OFFLINE` |
| `cpu`, `memory` | relevés par l'agent (`/proc/meminfo`) |
| `capacity` | `(mémoire − 1 Gio) / 256 Mio` — un Gio réservé au système |
| `last_heartbeat` | au-delà de `INSACLOUD_HEARTBEAT_TIMEOUT`, le nœud passe `OFFLINE` |

### `instances`

| Colonne | Notes |
|---|---|
| `container_id` | identifiant court Docker ; **change** après une reprise |
| `worker_id`, `distribution_id` | clés étrangères |
| `ssh_port`, `term_port`, `gui_port` | ports publiés sur le nœud |
| `root_password` | **chiffré** (Fernet) ; la clé vient du Vault Ansible |
| `migrations` | nombre de reprises sur un autre nœud |
| `status` | `running` · `expired` · `stopped` |

### `rentals`

| Colonne | Notes |
|---|---|
| `user_id`, `instance_id` | clés étrangères |
| `start_time`, `end_time` | UTC ; `end_time` est **la** donnée que lit le Faucheur |
| `status` | `ACTIVE` · `EXPIRED` · `STOPPED` |

### `login_attempts`

Journal des tentatives de connexion, purgé au-delà de 24 h. Cinq échecs sur un
compte en quinze minutes le verrouillent ; vingt depuis une même adresse IP la
bloquent.

---

## Index et contraintes

| Index | Rôle |
|---|---|
| `idx_rentals_end (status, end_time)` | la requête du Faucheur, exécutée toutes les dix secondes |
| `idx_instances_status (status)` | compter les machines actives par nœud |
| `idx_attempts_recent (created_at, username, ip)` | fenêtre glissante de l'anti-force-brute |
| `uq_worker_port_actif (worker_id, ssh_port)` | **unique partiel** : un port ne sert qu'une fois par nœud, mais seulement tant que l'instance tourne — après destruction, il redevient disponible |

Les clés étrangères sont activées explicitement (`PRAGMA foreign_keys=ON`) :
SQLite les ignore silencieusement sinon.

---

## Concurrence

Trois processus écrivent dans la même base : l'application (quatre workers
Gunicorn), le Faucheur, et les agents via l'API. En SQLite, le mode **WAL**
autorise des lectures pendant une écriture, ce qui rend cette cohabitation
possible ; `busy_timeout` de 15 s absorbe les collisions. En PostgreSQL, la
question ne se pose pas.

---

## Migrations

```bash
cd 2_webapp
alembic current                      # version appliquée
alembic upgrade head                 # appliquer
alembic downgrade -1                 # revenir d'un cran
alembic revision --autogenerate -m "ajout du champ X"
alembic check                        # le modèle et la base sont-ils alignés ?
```

`migrations/env.py` lit `DATABASE_URL`, la même variable que l'application :
une migration ne peut donc pas viser une autre base par inadvertance. Sur
SQLite, `render_as_batch` recrée la table et recopie les données, faute de quoi
toute modification autre qu'un simple ajout échouerait.

---

## Données de démonstration

```bash
python3 seed.py --reset --mots-de-passe
```

Trois comptes, trois nœuds dont un en panne simulée, et trois locations dans
des états différents — en cours, expirée, longue durée. Le script refuse de
s'exécuter si `INSACLOUD_ENV=production`.
