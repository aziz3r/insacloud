# API InsaCloud

Toutes les réponses sont en JSON. Les chemins sont ceux du cahier des charges.

Deux formes d'authentification, selon l'appelant :

| Appelant | Mécanisme | Portée |
|---|---|---|
| Utilisateur | cookie de session obtenu par `POST /login` | ses propres machines |
| Agent d'un worker | en-tête `X-Agent-Token` (jeton partagé) | `POST /workers/register` et `/workers/heartbeat` |

Les routes modifiantes servies en HTML exigent un jeton CSRF. Une requête
`Content-Type: application/json` en est dispensée : un formulaire HTML ne peut
pas émettre ce type, et une requête inter-origines déclenche un contrôle
préalable CORS auquel ce serveur ne répond pas.

---

## État du service

### `GET /health`

Sans authentification. Utilisé par la sonde Docker, le watchdog systemd et
l'étape de déploiement de l'intégration continue.

```bash
curl -s http://127.0.0.1:8088/health
```

```json
{
  "status": "ok",
  "database": "ok",
  "version": "1.0.0",
  "users": 3,
  "workers_total": 3,
  "workers_available": 2,
  "workers_offline": 1,
  "instances_running": 2,
  "instances_total": 5,
  "rentals_active": 2,
  "distributions": 3
}
```

`503` avec `"status": "degraded"` si la base est injoignable.

---

## Comptes

### `POST /register`

| Champ | Obligatoire | Règle |
|---|---|---|
| `username` | oui | 3 à 32 caractères, `[A-Za-z0-9._-]`, commence par une lettre ou un chiffre |
| `password` | oui | 10 caractères minimum, ni trop courant, ni égal à l'identifiant |
| `email` | non | adresse valide, unique |

```bash
curl -s -X POST http://127.0.0.1:8088/register \
  -H 'Content-Type: application/json' \
  -d '{"username":"etudiant","email":"etudiant@insa-cvl.fr","password":"Soutenance-2026!"}'
```

`201` → `{"id": 1, "username": "etudiant", "email": "…", "message": "Compte créé."}`
`400` identifiant, adresse ou mot de passe refusé · `409` déjà utilisé

### `POST /login`

```bash
curl -s -c cookies.txt -X POST http://127.0.0.1:8088/login \
  -H 'Content-Type: application/json' \
  -d '{"username":"etudiant","password":"Soutenance-2026!"}'
```

`200` → identité de l'utilisateur · `401` identifiants refusés
`429` compte verrouillé après cinq échecs, pendant quinze minutes

### `POST /logout`

`200` → `{"status": "logged_out"}`

---

## Catalogue

### `GET /distributions`

Distributions réellement proposables, c'est-à-dire actives **et** dont l'image
existe sur ce serveur — en mode démonstration, une seule image est construite.

```json
{
  "count": 3,
  "distributions": [
    {"name": "alpine", "label": "Alpine Linux 3.20", "version": "3.20",
     "docker_image": "insacloud_alpine", "hint": "Ultra-légère, démarre instantanément"}
  ],
  "modes": [{"name": "terminal", "label": "Terminal", "gui": false},
            {"name": "desktop", "label": "Bureau graphique", "gui": true}]
}
```

---

## Machines

### `GET /instances`

Locations de l'utilisateur connecté. **Le mot de passe root n'y figure jamais** :
il n'est renvoyé qu'une fois, à la création.

### `GET /instances/{id}`

Une location. `404` si elle n'existe pas **ou** appartient à quelqu'un d'autre :
la réponse est la même dans les deux cas, pour ne pas révéler son existence.

### `POST /rent`

| Champ | Défaut | Valeurs |
|---|---|---|
| `duration` | — | 1 à 120 minutes |
| `distribution` | `ubuntu` | `ubuntu`, `debian`, `alpine` |
| `mode` | `terminal` | `terminal`, `desktop` |

```bash
curl -s -b cookies.txt -X POST http://127.0.0.1:8088/rent \
  -H 'Content-Type: application/json' \
  -d '{"distribution":"alpine","mode":"terminal","duration":15}'
```

```json
{
  "id": 1,
  "name": "insacloud_etudiant_a1b2c3",
  "distribution": "alpine",
  "mode": "terminal",
  "status": "running",
  "worker": "worker1",
  "worker_ip": "192.168.56.11",
  "ssh_port": 8524,
  "ssh_command": "ssh root@192.168.56.11 -p 8524",
  "term_port": 8676,
  "gui_port": null,
  "expires_at": "2026-09-30 17:54:10",
  "root_password": "aZ3kR7mQp2XwT9bN",
  "migrations": 0
}
```

`400` durée, distribution ou mode refusé · `429` quota de trois machines atteint
`502` Docker a refusé la création · `503` aucun nœud disponible

### `POST /instances/{id}/stop`

Détruit la machine avant l'échéance. Idempotent : une machine déjà arrêtée
renvoie `200`.

### `POST /instances/{id}/extend`

`{"minutes": 10}` — prolonge à partir de la date de fin actuelle.

---

## Parc de nœuds

### `GET /workers` · `GET /workers/{id}`

Ouvert en lecture : aucune donnée d'utilisateur n'y figure. C'est ce que
consultent le tableau de supervision et l'intégration continue.

```json
{
  "count": 3,
  "workers": [
    {"id": 1, "hostname": "worker1", "ip": "192.168.56.11",
     "status": "AVAILABLE", "cpu": 2, "memory": 3072, "capacity": 8,
     "running_instances": 1, "reachable": true,
     "last_heartbeat": "2026-09-30 15:42:11"}
  ]
}
```

États : `AVAILABLE` joignable et sous sa capacité · `BUSY` joignable mais plein
· `OFFLINE` sans battement de cœur depuis `INSACLOUD_HEARTBEAT_TIMEOUT`.

### `POST /workers/register`

Appelé par `worker_agent.py` au démarrage du nœud.

```bash
curl -s -X POST https://192.168.56.10/workers/register \
  -H 'Content-Type: application/json' -H "X-Agent-Token: $JETON" \
  -d '{"hostname":"worker1","ip":"192.168.56.11","cpu":2,"memory":3072,"docker":true}'
```

`201` → identité et capacité attribuée · `400` `docker: false` ou champs manquants
`401` jeton refusé · `503` `INSACLOUD_AGENT_TOKEN` non défini côté contrôleur

La capacité est déduite de la mémoire : `(mémoire − 1 Gio) / 256 Mio`, un Gio
étant réservé au système du nœud.

### `POST /workers/heartbeat`

`{"hostname": "worker1", "status": "BUSY"}` — `status` est facultatif.

`200` · `404` avec `{"action": "register"}` si le nœud est inconnu : l'agent se
réinscrit alors de lui-même, ce qui permet de repartir d'une base vide.

---

## Codes de retour

| Code | Sens |
|---|---|
| `200` / `201` | succès |
| `400` | requête invalide |
| `401` | authentification requise ou refusée |
| `403` | jeton CSRF invalide |
| `404` | ressource inexistante, ou appartenant à un autre compte |
| `413` | corps de requête supérieur à 16 Kio |
| `429` | quota atteint, ou verrouillage anti-force-brute |
| `502` | Docker a refusé l'opération |
| `503` | aucun nœud disponible, ou base injoignable |
