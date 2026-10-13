# Déroulé de la soutenance

Toutes les commandes ci-dessous ont été exécutées et vérifiées. Les chemins
supposent le dépôt dans `~/Developer/insacloud`.

---

## Avant d'entrer dans la salle

```bash
colima start --cpu 4 --memory 6 --disk 40
cd ~/Developer/insacloud
docker compose -f docker-compose.yml -f docker-compose.noeuds.yml up -d
```

Les deux fichiers : la pile de la plateforme, et le parc à deux nœuds sur
lequel se fait la partie 3. Vérifier que tout est là — trois conteneurs de
plateforme `healthy`, deux nœuds `Up` :

```bash
docker compose -f docker-compose.yml -f docker-compose.noeuds.yml ps
```

Vérifier que les deux nœuds se sont inscrits auprès du contrôleur :

```bash
curl -s http://127.0.0.1:8088/workers | python3 -m json.tool
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

## Partie 3 — Le parc et la reprise sur panne (10 min)

> **Ce qu'il faut dire d'emblée, et sans détour.** Le déploiement de référence
> est Vagrant + Ansible : un contrôleur et trois workers, chacun une machine
> virtuelle. Aucun hyperviseur ne démarre sur ce poste : VMware n'a pas ses
> interfaces `vmnet` (son extension système n'est pas installée),
> VirtualBox fait tuer la VM au démarrage (`signal 9`, support ARM
> expérimental). La démonstration se fait donc sur **deux nœuds en
> conteneurs** : même transport SSH, même agent, même bascule. Ce qui est
> simulé, et seulement cela : les deux nœuds partagent le démon Docker de
> l'hôte au lieu d'en avoir un chacun.

### Monter le parc à deux nœuds

```bash
cd ~/Developer/insacloud
docker compose -f docker-compose.yml -f docker-compose.noeuds.yml up -d
```

Trois conteneurs de plateforme et deux nœuds. Le service `cles` fabrique au
passage la clé de pilotage du contrôleur, une seule fois, dans un volume
partagé — c'est ce que fait le rôle Ansible `controller_key` sur un vrai parc.

Montrer que le contrôleur parle vraiment à ses nœuds **par SSH** :

```bash
docker exec insacloud-web docker -H ssh://insacloud@noeud1 info --format '{{.ServerVersion}}'
```

Et que les deux nœuds se sont **inscrits d'eux-mêmes** : ouvrir
**http://127.0.0.1:8088/workers**. Les deux nœuds y sont, avec leurs cœurs,
leur mémoire, leur **capacité calculée** (quatre cœurs → douze machines), leur
nombre de machines en cours et la date de leur dernier battement de cœur.
Personne ne les a déclarés dans un fichier : leur agent a appelé le contrôleur
au démarrage. Cette vue est ouverte en lecture — aucune donnée d'utilisateur
n'y figure — c'est la même que consulte la chaîne d'intégration.

> Le nœud `local` y apparaît `OFFLINE`, avec `reachable: false` : c'est
> l'ancien mode mono-hôte, retiré du parc. La plateforme le signale et ne lui
> envoie plus rien — c'est la détection qui fonctionne, pas une panne. Si la
> question vient, c'est une bonne occasion de montrer que l'ordonnanceur
> **exclut** un nœud hors ligne (`select_available_worker`).

### Le scénario de l'énoncé, en direct

1. Louer une machine depuis le tableau de bord. Dès qu'il y a plus d'un nœud,
   le tableau de bord affiche sous chaque machine **celui qui l'héberge** —
   `noeud1 · 127.0.0.1`. Noter le nœud et le port.
2. Vérifier qu'elle répond, puis **couper le nœud qui l'héberge** :

   ```bash
   docker stop insacloud-noeud1
   ```

3. Suivre le Faucheur pendant que ça se passe :

   ```bash
   docker logs -f insacloud-faucheur
   ```

   Le journal raconte la séquence dans l'ordre : le nœud devient injoignable
   pour la réconciliation — **et rien n'est détruit**, un nœud muet n'est pas
   interprété ; puis l'absence de battement de cœur le fait passer `OFFLINE` ;
   puis la machine est recréée sur l'autre nœud.

4. Mesuré lors de la dernière répétition : nœud coupé à `13:23:16`, machine
   en service sur `noeud2` à `13:23:47`. **Trente et une secondes**, avec le
   délai de détection réglé à 30 s dans `.env`.

5. Recharger le tableau de bord. Nouveau port, nouveau mot de passe,
   **même location et même durée** — et un bandeau qui dit à l'utilisateur ce
   qui s'est passé : « Reprise sur un autre nœud ». Le compteur `migrations`
   est passé à 1.

6. Vérifier que la machine répond vraiment sur son nouveau port :

   ```bash
   nc -w 4 127.0.0.1 <nouveau_port> < /dev/null | head -1
   ```

   Elle annonce sa bannière SSH. Ce n'est pas une ligne dans une base : la
   machine tourne.

7. Rallumer le nœud — son agent se réinscrit tout seul en moins de dix
   secondes, et le parc est de nouveau complet :

   ```bash
   docker start insacloud-noeud1
   ```

### Ce que la bascule ne rend pas, et pourquoi le dire

Les fichiers créés dans l'ancienne machine sont perdus : la reprise recrée un
conteneur, elle ne déplace pas un disque. Sans stockage partagé entre nœuds,
c'est la limite assumée — et c'est pour cela que le Faucheur archive le
travail avant toute destruction *prévue* (partie 2). Une panne n'est pas
prévue : il n'y a rien à archiver sur un nœud qui ne répond plus.

### Le parc Vagrant, pour la question qui viendra

Le `Vagrantfile` et les quatre playbooks sont là, lisibles, et c'est le
déploiement que le sujet demande. Sur un poste où l'hyperviseur fonctionne :

```bash
WORKERS=2 CTRL_MEM=1536 WORKER_MEM=1280 vagrant up --provider=vmware_desktop
cd 3_ansible && ansible-playbook -i inventaire_vagrant.py site.yml --ask-vault-pass
```

L'inventaire est **dynamique** : il interroge Vagrant, découvre les VM
réellement démarrées et lit la clé SSH que Vagrant déclare lui-même — il
s'adapte aux quatre providers sans qu'on modifie une ligne. Rejouer le
playbook affiche `changed=0` partout : c'est la preuve qu'Ansible décrit un
état et n'enchaîne pas des actions. `ansible-lint --profile production` passe
sur les neuf rôles.

### Revenir au mode mono-hôte

```bash
docker compose -p insacloud up -d --remove-orphans
```

---

## Partie 4 — La qualité (5 min)

```bash
cd 2_webapp
.venv/bin/python -m pytest          # 169 tests, environ 20 s
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
| Un nœud reste absent de `/workers` | Son agent n'a pas pu joindre le contrôleur : `docker logs insacloud-noeud1 \| tail -20` |
| « Clé publique du contrôleur absente » | Le service `cles` n'a pas tourné : `docker compose -f docker-compose.yml -f docker-compose.noeuds.yml up cles` |
| La reprise ne part pas | Le délai de détection est dans `.env` (`INSACLOUD_HEARTBEAT_TIMEOUT=30`) : attendre ce délai plus un cycle de Faucheur, soit ~40 s. |
| Tout part de travers | `docker compose -p insacloud up -d --remove-orphans` : retour au mode mono-hôte, qui suffit aux parties 1, 2 et 4. |
| `vagrant up` échoue sur VMware | L'extension système n'est pas installée sur ce poste (`systemextensionsctl list` renvoie `0 extension(s)`). C'est annoncé en partie 3, pas une surprise. |
| Plus de place disque | `docker system df`, puis `docker image prune` (sans `-a`, qui effacerait les six images) |
