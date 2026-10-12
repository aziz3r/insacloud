# Déroulé de la soutenance

Toutes les commandes ci-dessous ont été exécutées et vérifiées. Les chemins
supposent le dépôt dans `~/Developer/insacloud`.

---

## Avant d'entrer dans la salle

```bash
colima start --cpu 4 --memory 6 --disk 40
cd ~/Developer/insacloud && docker compose -p insacloud up -d
```

Vérifier que les trois conteneurs sont `healthy` :

```bash
docker compose -p insacloud ps
```

Vérifier que les **six** images de machines sont là — sans elles, le mode
bureau n'apparaît pas dans le formulaire :

```bash
docker images --filter 'reference=insacloud_*'
```

Attendu : `alpine`, `alpine_desktop`, `debian`, `debian_desktop`, `ubuntu`,
`ubuntu_desktop`, plus `web`.

Ouvrir **http://127.0.0.1:8088** et se connecter.

---

## Partie 1 — Le parcours utilisateur (5 min)

1. **Inscription** — montrer que la politique de mot de passe est vérifiée
   côté serveur, pas seulement dans le navigateur.
2. **Louer une machine** — trois distributions, deux modes, durée de 1 à
   120 minutes. Prendre *Alpine · Bureau graphique · 15 min* : c'est la plus
   rapide à démarrer et la plus spectaculaire.
3. **Le mot de passe s'affiche une seule fois.** Recharger la page : il a
   disparu. Le reprendre dans le coffre en ressaisissant son mot de passe de
   compte — le coffre s'ouvre cinq minutes.
4. **Bureau XFCE dans le navigateur** — cliquer le lien « Bureau ». noVNC
   demande un mot de passe : ce sont les 8 premiers caractères du mot de passe
   root (limite du protocole VNC).
5. **Terminal web** — montrer que le port ne donne pas un shell ouvert mais
   une invite `login`.
6. **SSH** — copier la commande affichée, se connecter depuis un terminal.

### Si la prof demande « et en ligne de commande ? »

```bash
curl -s http://127.0.0.1:8088/health | jq
curl -s -H 'Accept: application/json' http://127.0.0.1:8088/distributions | jq
```

La même URL sert du HTML ou du JSON selon l'en-tête `Accept` : il n'y a pas un
site d'un côté et une API de l'autre.

---

## Partie 2 — Ce qui tourne tout seul (5 min)

### Le durcissement imposé

```bash
docker inspect <nom_machine> --format '{{json .HostConfig}}' | jq '{RestartPolicy, Memory, MemoryReservation, NanoCpus, PidsLimit, CapDrop, SecurityOpt}'
```

Attendu : `always`, 256 Mio + réservation 128 Mio, 1 cœur, `pids-limit 512`,
`cap-drop ALL`, `no-new-privileges`.

### L'identité par étiquette

```bash
docker ps -a --filter "label=insacloud.role=rented-machine"
```

Expliquer pourquoi : le Faucheur reconnaissait autrefois les machines à leur
préfixe de nom, et il a fini par détruire les conteneurs de la plateforme
elle-même. Un nom est un affichage, une étiquette est une identité.

### L'expiration

Louer une machine **1 minute**, puis regarder le Faucheur la détruire :

```bash
docker logs -f insacloud-faucheur
```

### La sauvegarde du travail avant destruction

Écrire quelque chose dans une machine, l'arrêter, puis la récupérer :

```bash
docker exec <nom_machine> sh -c 'echo "TP INSA" > /root/rapport.txt'
# arrêter la machine depuis le tableau de bord, puis :
curl -s -b jar -H 'Accept: application/json' http://127.0.0.1:8088/archives | jq
```

L'archive porte son empreinte SHA-256 : on peut vérifier que le fichier
téléchargé est exactement celui qui a été produit.

---

## Partie 3 — Le cluster et la reprise sur panne (10 min)

> **Attention mémoire.** Ce poste a 8 Go. Colima en prend 6 : il faut l'arrêter
> avant de monter les VM. La pile compose sera indisponible pendant cette
> partie, c'est normal.

```bash
docker compose -p insacloud down
colima stop
```

### Monter le parc

Un contrôleur et **deux** workers : la reprise exige un nœud de repli, avec un
seul worker elle répond 503.

```bash
cd ~/Developer/insacloud
WORKERS=2 CTRL_MEM=1536 WORKER_MEM=1280 vagrant up --provider=vmware_desktop
```

### Déployer

L'inventaire **dynamique** interroge Vagrant : il découvre les VM réellement
démarrées et lit la clé SSH que Vagrant déclare, donc il s'adapte au provider
sans rien modifier.

```bash
cd 3_ansible
ansible-playbook -i inventaire_vagrant.py site.yml --ask-vault-pass
```

### Montrer l'idempotence

Rejouer la même commande : le second passage doit afficher `changed=0` partout.
C'est la preuve qu'Ansible décrit un état, et n'enchaîne pas des actions.

### Le scénario de l'énoncé

1. Ouvrir `https://192.168.56.10`, louer une machine, noter sur quel worker
   elle a été placée (`/workers` le dit).
2. Éteindre ce worker :

   ```bash
   vagrant halt worker1
   ```

3. Suivre le Faucheur sur le contrôleur :

   ```bash
   vagrant ssh controller -c 'journalctl -u insacloud-faucheur -f'
   ```

4. Au bout de **90 secondes** sans battement de cœur, le nœud passe `OFFLINE`
   et la machine est recréée sur `worker2`. Recharger le tableau de bord :
   nouveau port, nouveau mot de passe, **même location et même durée**.
5. Le compteur `migrations` de la machine est passé à 1 : c'est la preuve
   observable de la reprise.
6. Rallumer le worker — son agent se réinscrit tout seul en moins de dix
   secondes :

   ```bash
   vagrant up worker1
   ```

### Revenir à la pile compose après

```bash
vagrant halt
colima start --cpu 4 --memory 6 --disk 40
cd ~/Developer/insacloud && docker compose -p insacloud up -d
```

---

## Partie 4 — La qualité (5 min)

```bash
cd 2_webapp
.venv/bin/python -m pytest          # 147 tests, environ 20 s
.venv/bin/ruff check .
.venv/bin/python -m bandit -c pyproject.toml -r .
```

Les tests ne lancent aucun conteneur : `run_docker` est remplacé par une
fonction espion. On vérifie donc que la commande imposée par l'énoncé part bien
telle quelle, sur une machine qui n'a même pas Docker.

Montrer la chaîne d'intégration au vert sur GitHub :
<https://github.com/aziz3r/insacloud/actions>

Onze travaux, et aucune image ne part au registre si l'un échoue.

---

## Questions probables, et les réponses

**« Pourquoi des conteneurs et pas des VM ? »**
Une VM démarre en trente secondes et réserve plusieurs centaines de Mo pour son
noyau. Un conteneur réutilise celui de l'hôte : il démarre en moins d'une
seconde. Pour une location à la minute, c'est décisif. L'énoncé le tranche
d'ailleurs : « des conteneurs dans notre cas ».

**« Pourquoi piloter Docker par SSH et pas par son API ? »**
Qui parle au démon Docker d'une machine en est root. Exposer le port 2375
mettrait toute la plateforme à la merci d'une erreur de pare-feu. Avec
`docker -H ssh://`, aucun port Docker n'est ouvert et l'authentification est
celle de SSH, par clé.

**« Où est stocké le mot de passe root ? »**
Chiffré avec Fernet, clé distincte de celle des sessions, rangée dans le Vault
Ansible. Il n'est jamais passé par un cookie — on l'y avait mis au début, et
une session Flask est **signée mais pas chiffrée** : n'importe qui pouvait la
décoder en base64.

**« La reprise rend-elle les fichiers ? »**
Non, et c'est assumé. Ils vivaient dans la couche inscriptible du conteneur,
sur un nœud devenu injoignable. Sans stockage partagé, seul le service repart.
En revanche, à l'expiration et à l'arrêt manuel, le travail est archivé et
téléchargeable sept jours.

**« Comment ajoute-t-on un worker ? »**
On l'allume. Aucune adresse n'est écrite côté contrôleur : l'agent du nœud
s'inscrit tout seul au démarrage, puis signale sa présence toutes les
30 secondes.

---

## Si quelque chose casse

| Symptôme | Réponse |
|---|---|
| Le mode bureau n'apparaît pas | Les images `*_desktop` ne sont pas construites. Le catalogue refuse d'proposer un choix qu'il ne peut pas honorer. |
| `port is already allocated` | Un conteneur d'une démo précédente traîne : `docker ps -a --filter "label=insacloud.role=rented-machine"` |
| Le site ne répond pas | `docker compose -p insacloud logs web --tail 50` |
| `vagrant up` échoue sur VMware | Vérifier que le VMware Utility tourne : `sudo launchctl list \| grep vagrant` |
| Plus de place disque | `docker system df`, puis `docker image prune` (sans `-a`, qui effacerait les six images) |
