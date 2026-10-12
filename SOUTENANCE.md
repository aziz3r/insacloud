# Script de soutenance — InsaCloud

**15 minutes, trois intervenants, démonstration en direct intégrée au discours.**

Principe : on ne fait pas « d'abord on explique, ensuite on montre ». À chaque
geste de la démonstration, celui qui parle nomme **l'outil employé** et **la
fonctionnalité du cahier des charges** qu'il réalise. La prof doit pouvoir
cocher sa grille sans avoir à nous le demander.

---

## Avant d'entrer dans la salle

À faire 20 minutes avant, pas 2 :

```bash
colima start --cpu 4 --memory 6 --disk 40
cd ~/Developer/insacloud && docker compose -p insacloud up -d
docker compose -p insacloud ps              # les 3 doivent être « healthy »
docker images --filter 'reference=insacloud_*'   # les 6 images doivent être là
```

Ouvrir et laisser prêts, dans cet ordre d'onglets :

1. `http://127.0.0.1:8088` — connecté, tableau de bord **vide**
2. `https://github.com/aziz3r/insacloud/actions` — la chaîne au vert
3. Un terminal plein écran dans `~/Developer/insacloud`
4. Le `README.md` du dépôt, prêt à montrer les schémas

Si le cluster Vagrant est monté : un quatrième onglet sur `https://192.168.56.10`
et un second terminal prêt pour `vagrant halt worker1`.

> **Règle d'or** : personne ne tape une commande qu'il n'a pas répétée. Chaque
> commande de ce script a été exécutée au moins une fois avant la soutenance.

---

## Répartition

| Qui | Quand | Durée | Rôle |
|---|---|---|---|
| **Personne 1** | 0:00 – 3:30 | 3 min 30 | Le problème, l'architecture, l'infrastructure |
| **Personne 2** | 3:30 – 9:00 | 5 min 30 | Le parcours utilisateur en direct |
| **Personne 3** | 9:00 – 13:30 | 4 min 30 | Les scénarios de l'énoncé, la sécurité, la CI |
| **Personne 1** | 13:30 – 15:00 | 1 min 30 | Conformité, ajouts, conclusion |

---

# PERSONNE 1 — Le problème et l'infrastructure
### 0:00 → 3:30

## 0:00 — Ouvrir (30 s)

> « InsaCloud est un mini-fournisseur de cloud. Un utilisateur s'inscrit,
> choisit une distribution Linux et une durée, et obtient en quinze secondes
> une machine à lui, avec un accès root. À l'échéance, elle disparaît toute
> seule.
>
> Derrière ce clic il y a quatre machines, un ordonnanceur, un démon de
> nettoyage, un coffre à secrets et onze contrôles automatiques. C'est ce que
> nous allons dérouler. »

## 0:30 — Le choix fondateur (45 s)

**Montrer** : le schéma d'architecture du README.

> « Première décision : des **conteneurs**, pas des machines virtuelles. Une VM
> démarre en trente secondes et réserve des centaines de mégaoctets rien que
> pour son noyau. Un conteneur réutilise le noyau de l'hôte : il démarre en
> moins d'une seconde. Pour une location **à la minute**, c'est décisif — et
> l'énoncé le tranche lui-même : "des conteneurs dans notre cas".
>
> Deuxième décision : séparer **qui décide** de **qui exécute**. Le contrôleur
> porte le site, la base et l'ordonnanceur. Il n'exécute aucune machine louée
> et n'a même pas de démon Docker, seulement le client. Les trois workers
> portent Docker et les conteneurs. Un utilisateur qui s'échapperait de son
> conteneur tomberait sur un worker : pas de base, pas de clés, pas de code. »

## 1:15 — Comment le contrôleur pilote les workers (45 s)

**Taper** :
```bash
docker -H ssh://insacloud@worker1 ps
```
*(ou, en mode compose, montrer `2_webapp/workers.py` — la fonction `docker_command`)*

> « On aurait pu exposer l'API Docker sur le réseau, port 2375. C'est simple et
> c'est une très mauvaise idée : qui parle au démon Docker d'une machine en est
> root. Une erreur de pare-feu et toute la plateforme tombe.
>
> On passe donc par **SSH**, que le client Docker sait faire nativement. Aucun
> port Docker n'est ouvert, l'authentification est celle de SSH par clé, et le
> compte de service n'a pas de mot de passe. »

→ **Fonctionnalité 4** (conteneurisation) · **Fonctionnalité 14** (sécurité)

## 2:00 — L'infrastructure comme code (1 min)

**Montrer** : le `Vagrantfile`, puis `3_ansible/site.yml`.

> « Rien n'est installé à la main. **Vagrant** décrit les quatre machines
> virtuelles — un contrôleur en .10, trois workers en .11, .12, .13 — avec
> leur mémoire, leurs cœurs, leur réseau privé. Le fichier gère quatre
> *providers*, dont VMware, parce que VirtualBox ne tourne pas sur une puce
> Apple.
>
> **Ansible** les configure : neuf rôles, quatre playbooks. Pare-feu, clé de
> pilotage, Docker, certificats TLS, images, agent, proxy inverse,
> application. »

**Taper** (si le cluster est monté) :
```bash
cd 3_ansible && ansible-playbook -i inventaire_vagrant.py site.yml --vault-password-file .vault_pass
```

> « Et surtout : le playbook est **idempotent**. Le relancer sur un serveur déjà
> configuré ne change rien. La preuve se lit dans le compte rendu — au second
> passage, `changed=0` sur les quatre nœuds. Ce n'est pas "ça ne fait rien la
> deuxième fois" : c'est "il vérifie l'état et n'agit que s'il diffère". »

→ **Fonctionnalité 5** (Vagrant) · **Fonctionnalité 6** (Ansible)

## 3:00 — Les secrets (30 s)

**Montrer** : `3_ansible/group_vars/insacloud/vault.yml` (illisible à l'écran).

> « Les secrets vivent dans un **Ansible Vault**, chiffré en AES-256. Ce fichier
> est versionné dans Git tel quel : sans la phrase de passe, il ne dit rien.
> Il contient la clé de session Flask, la clé du coffre, et le jeton des
> agents — trois clés distinctes, jamais la même. »

→ **Fonctionnalité 6** · **Fonctionnalité 14**

**Transition** : « *[Prénom 2]* va vous montrer ce que ça donne du côté de
l'utilisateur. »

---

# PERSONNE 2 — Le parcours utilisateur, en direct
### 3:30 → 9:00

> ⚠️ Tout ce qui suit se fait **en cliquant**, pas en racontant. Parler
> pendant que ça charge.

## 3:30 — Inscription et connexion (40 s)

**Faire** : créer un compte en direct, puis se connecter.

> « L'application est en **Flask**, servie par **Gunicorn** avec quatre
> processus ouvriers, derrière **nginx** qui termine le TLS. Flask seul traite
> une requête à la fois et le dit lui-même : à ne pas utiliser en production.
>
> La politique de mot de passe est vérifiée **côté serveur**. Un contrôle fait
> seulement dans le navigateur se contourne en coupant JavaScript. Les mots de
> passe de compte sont **hachés** en pbkdf2-sha256 : opération à sens unique,
> on ne peut pas les retrouver. Et cinq échecs de connexion verrouillent le
> compte. »

→ **Fonctionnalité 2** (API Web Flask) · **Fonctionnalité 14**

## 4:10 — Le catalogue (30 s)

**Montrer** : les trois distributions, les deux modes.

> « Trois distributions — Ubuntu, Debian, Alpine — en deux modes, soit **six
> images** construites depuis **quatre Dockerfiles**. Le mode bureau s'active
> par un simple `--build-arg DESKTOP=1`.
>
> Le catalogue n'est pas écrit en dur : il est **en base**, et l'application
> interroge Docker avant d'afficher un choix. Si une image n'est pas
> construite, la distribution n'apparaît pas — on ne propose pas ce qu'on ne
> peut pas livrer. »

→ **Fonctionnalité 3** (base de données) · **Fonctionnalité 4**

## 4:40 — Louer (50 s)

**Faire** : Ubuntu · Bureau graphique · 10 minutes · *Louer la machine*.

Pendant que ça démarre :

> « Ce qui part côté serveur, c'est exactement la commande imposée par
> l'énoncé :
>
> `docker run -d --restart=always -p <port>:22 <image>`
>
> On la pilote par **`subprocess`**, pas par un SDK — comme ça chaque option se
> lit telle quelle dans le code. Et jamais avec `shell=True` : les arguments
> sont passés en **liste**, donc aucun interpréteur ne relit la chaîne. Sinon
> un utilisateur nommerait sa machine `x; rm -rf /`.
>
> S'y ajoutent le durcissement : `--cap-drop ALL` puis seulement les dix
> capacités dont `sshd` a besoin, `no-new-privileges`, une limite de processus,
> de CPU, et la mémoire — **réservation et limite**, comme le demande le cours.
>
> Le port vient de la plage 8000–9000, et son unicité est garantie par un
> **index unique partiel** en base, limité aux machines actives. Deux requêtes
> simultanées ne peuvent pas prendre le même port. »

→ **Fonctionnalité 9** (service de location) · **Fonctionnalité 10**
(déploiement des instances) · **Fonctionnalité 8** (ordonnanceur)

## 5:30 — Le secret montré une fois (40 s)

**Montrer** : le bandeau du mot de passe root.

> « Le mot de passe root est tiré au sort, seize caractères, environ 90 bits.
> Il est affiché **une seule fois**, puis chiffré avec **Fernet** avant d'être
> écrit en base — clé distincte de celle des sessions.
>
> Une erreur qu'on a commise au début : on l'avait mis dans le cookie de
> session. Or une session Flask est **signée, pas chiffrée** : n'importe qui
> pouvait la décoder en base64. On ne stocke plus que l'identifiant de la
> machine, et le secret est relu en base. »

**Faire** : recharger la page → le mot de passe a disparu. Puis ressaisir le
mot de passe du compte → le coffre s'ouvre cinq minutes.

→ **Fonctionnalité 14** (protection des secrets)

## 6:10 — Les trois accès (1 min 20)

**Faire, dans l'ordre** :

1. **SSH** — copier la commande, se connecter depuis le terminal.
   > « L'accès demandé par l'énoncé. Par mot de passe, ou par clé si
   > l'utilisateur a enregistré sa clé publique — dans ce cas la machine
   > n'accepte **plus que** la clé. »

2. **Terminal web** — ouvrir le lien.
   > « **ttyd**. Attention : le port ne donne pas un shell ouvert, il lance
   > `login`. Il faut s'authentifier. »

3. **Bureau graphique** — ouvrir, saisir le mot de passe VNC.
   > « **Xvnc**, **XFCE** et **noVNC**. Cinq processus cohabitent dans ce
   > conteneur : sshd, ttyd, le serveur d'affichage, le bureau et la
   > passerelle web. Or un conteneur n'exécute qu'un processus — d'où
   > **supervisord** en PID 1, qui les démarre et relance celui qui meurt. »

→ **Fonctionnalité 11** (accès SSH) · **Fonctionnalité 4**

## 7:30 — L'expiration (1 min)

**Faire** : louer une machine **1 minute**, et pendant qu'elle vit :

```bash
docker logs -f insacloud-faucheur
```

> « Personne ne viendra cliquer sur "supprimer" à trois heures du matin. Un
> service séparé, qu'on appelle le **Faucheur**, lit la base toutes les dix
> secondes et détruit ce qui a expiré.
>
> Il fait aussi de la **réconciliation** : la base dit ce qui *devrait*
> exister, Docker ce qui existe *vraiment*. Un conteneur orphelin est
> supprimé, une instance sans conteneur passe à "arrêtée". Avec une règle
> essentielle : un nœud injoignable est **ignoré**, jamais interprété comme
> vide — sinon on marquerait arrêtées des machines qui tournent très bien.
>
> Et il reconnaît ses machines à une **étiquette Docker**, jamais à leur nom.
> Au début il les reconnaissait par préfixe — et il a fini par détruire les
> conteneurs de la plateforme elle-même. »

**Montrer** : la machine disparaît, le quota repasse à 0.

→ **Fonctionnalité 12** (gestion de l'expiration)

## 8:30 — L'ajout : la sauvegarde du travail (30 s)

**Montrer** : la section « Archives de mon travail », télécharger une archive.

> « Ça, l'énoncé ne le demande pas. Détruire un conteneur détruit sa couche
> inscriptible : l'utilisateur perd tout. Juste avant le `docker rm -f`, on
> fait un `tar` de `/root` et `/home` qu'on lit au fil de l'eau, et
> l'utilisateur le télécharge pendant sept jours.
>
> Avec une règle qui prime sur tout : l'archivage **ne bloque jamais** la
> destruction. Sinon une machine expirée sur un nœud en panne resterait en vie
> indéfiniment. »

→ **Au-delà de l'énoncé**

**Transition** : « *[Prénom 3]* va vous montrer ce qui se passe quand ça casse. »

---

# PERSONNE 3 — Les scénarios, la sécurité, la chaîne
### 9:00 → 13:30

## 9:00 — Le parc qui se gère seul (50 s)

**Faire** :
```bash
curl -s -H 'Accept: application/json' http://127.0.0.1:8088/workers | jq
```

> « Le contrôleur ne contient **aucune adresse de worker**. Chaque nœud porte un
> petit **agent**, écrit en bibliothèque standard seulement — pas de
> dépendance à installer sur les workers. Au démarrage il se présente, puis il
> signale sa présence toutes les trente secondes.
>
> Trois états : `AVAILABLE`, `BUSY` quand le nœud est plein, `OFFLINE` après
> quatre-vingt-dix secondes de silence. La distinction compte : un nœud saturé
> est en bonne santé, il ne faut surtout pas migrer ses machines.
>
> Conséquence concrète : pour ajouter un quatrième worker, on l'allume. C'est
> tout. »

→ **Fonctionnalité 7** (Worker Agent) · **Fonctionnalité 8** (ordonnanceur)

## 9:50 — Le Test 6 de l'énoncé : la panne (1 min 40)

> ⚠️ **Si le cluster Vagrant est monté**, faire la manipulation en direct.
> **Sinon**, lire le repli à la fin de cette section — et le dire franchement.

**Faire** :
```bash
vagrant halt worker1
vagrant ssh controller -c 'journalctl -u insacloud-faucheur -f'
```

> « L'énoncé décrit précisément ce test : on éteint `worker1`, et on vérifie
> que `worker1` passe `OFFLINE` et que l'instance repart sur `worker2`.
>
> L'agent cesse de battre. Au bout de quatre-vingt-dix secondes le contrôleur
> déclare le nœud hors ligne, et recrée ses machines sur un nœud disponible.
>
> Ce qui compte, c'est ce que le code **ne** touche **pas** : la table des
> locations. C'est la décision qui structure toute la base — on a séparé
> `Instance`, qui décrit un conteneur, de `Rental`, qui décrit l'engagement
> envers l'utilisateur. Si on avait fondu les deux en une table, plus court à
> écrire, la reprise serait **impossible** : on ne pourrait pas recréer la
> machine ailleurs sans toucher à la durée réservée.
>
> L'utilisateur garde donc sa location et sa durée ; il reçoit un nouveau port
> et un nouveau mot de passe. Et un compteur `migrations` passe à 1 : c'est la
> preuve observable, celle que vérifie notre test d'intégration. »

**Montrer** : le tableau de bord — nouveau port, même échéance.

> « Une limite qu'on assume : la reprise ne rend **pas les fichiers**. Ils
> vivaient dans la couche inscriptible, sur un nœud devenu injoignable. Sans
> stockage partagé, seul le service repart. Un volume répliqué changerait la
> nature du projet. »

**Repli si le cluster n'est pas disponible** :
> « La reprise ne peut pas tourner sur ce poste : le parc demande onze
> gigaoctets de mémoire, la machine en a huit. Le scénario a été mesuré sur un
> cluster réel à quatre nœuds — c'est dans le dossier technique :
> `worker1` éteint, `OFFLINE` à quatre-vingt-dix secondes, machine recréée sur
> `worker2`, location intacte, `migrations=1`, et `worker1` de retour en moins
> de dix secondes. »

→ **Fonctionnalité 13** (haute disponibilité) · **Test 6 de l'énoncé**

## 11:30 — Les six scénarios d'intégration (40 s)

**Faire** :
```bash
cd 2_webapp && ./tests/integration.sh http://127.0.0.1:8088
```

> « L'énoncé liste six tests. Ils sont scriptés et rejouables :
> infrastructure, configuration Ansible, application, location avec connexion
> SSH réelle, expiration, et panne d'un nœud.
>
> À côté, **147 tests unitaires** en vingt secondes. Et ils ne lancent aucun
> conteneur : `run_docker` est remplacé par une fonction espion. On vérifie
> donc que la commande imposée part bien telle quelle, sur un agent
> d'intégration qui n'a même pas Docker. »

→ **Fonctionnalité 16** (tests d'intégration)

## 12:10 — La sécurité DevSecOps (40 s)

**Montrer** : l'onglet GitHub Actions, la liste des travaux.

> « Le "Sec" de DevSecOps, c'est que la sécurité est un contrôle **automatique**,
> pas un audit de fin de projet. Six familles :
>
> — **bandit** cherche les motifs dangereux dans le code : injection, secret en
> dur, appel système construit depuis une entrée utilisateur ;
> — **pip-audit** confronte nos dépendances aux bases de CVE ;
> — **gitleaks** cherche les secrets commis par erreur, **y compris dans
> l'historique** — effacer un secret dans un commit suivant ne l'efface pas ;
> — **nuclei** et un script maison attaquent l'application **en marche** ;
> — **Trivy** scanne les images : aucune faille corrigeable HIGH ou CRITICAL
> ne passe ;
> — **Syft** produit le SBOM, l'inventaire de ce qu'on livre. »

→ **Fonctionnalité 14** (sécurité DevSecOps)

## 12:50 — La chaîne, et ce qu'elle a trouvé (40 s)

> « Onze travaux à chaque envoi. Et ils **bloquent** : aucune image n'atteint le
> registre si l'un échoue. Ce n'est pas décoratif — nos propres images ont été
> refusées par notre propre scan, et il a fallu changer de base.
>
> Dix défauts ont été trouvés en **faisant tourner** le projet, pas en le
> relisant : le Faucheur qui se supprimait lui-même, le mot de passe
> journalisé en clair, une course entre les quatre processus Gunicorn, une
> dépendance qui n'existait pas pour le Python de la cible. Chacun a maintenant
> son test ou son garde-fou — la chaîne vérifie même sa propre syntaxe shell
> avant de démarrer. »

→ **Fonctionnalité 15** (CI/CD)

**Transition** : « *[Prénom 1]* conclut. »

---

# PERSONNE 1 — Conformité, ajouts, conclusion
### 13:30 → 15:00

## 13:30 — La grille (50 s)

**Montrer** : le `README.md`, section structure du dépôt.

> « Les dix-sept fonctionnalités de l'énoncé sont traitées. Rapidement, l'outil
> employé pour chacune :
>
> Git et un environnement épinglé pour la préparation ; Flask et Gunicorn pour
> l'API ; SQLAlchemy et Alembic pour la base — sept tables, migrations
> versionnées ; Docker et supervisord pour la conteneurisation ; Vagrant pour
> l'infrastructure ; Ansible pour l'automatisation, neuf rôles idempotents ;
> un agent maison pour l'enregistrement ; un ordonnanceur qui prend le nœud
> joignable le moins chargé ; le Faucheur pour l'expiration et la reprise ;
> bandit, pip-audit, gitleaks, nuclei, Trivy et Syft pour la sécurité ;
> GitHub Actions pour la chaîne ; pytest pour les tests.
>
> Et pour la documentation : le README, la doc de l'API, celle de la base,
> quatre schémas — et un **guide technique de 93 pages** qui explique tout le
> projet depuis les notions de base. »

→ **Fonctionnalité 1, 17** et récapitulatif

## 14:20 — Ce qu'on a ajouté (30 s)

> « Sept choses au-delà de ce qui était demandé :
>
> un parc **distribué à trois workers** alors qu'un seul hôte suffisait ;
> le **terminal web** et le **bureau graphique** en plus du SSH ;
> un **coffre à secrets** qui réaffiche le mot de passe après
> re-authentification ;
> un **watchdog** systemd qui répare le service tout seul ;
> un **inventaire dynamique** qui interroge Vagrant, ce qui rend le
> déploiement indépendant du provider ;
> la **sauvegarde du travail** avant destruction ;
> et des **schémas engendrés depuis leur description**, que la chaîne
> régénère et refuse de laisser vieillir. »

## 14:50 — Fermer (10 s)

> « Le projet tourne, il est testé, et il se redéploie en une commande. Nous
> sommes à votre disposition pour les questions. »

---

## Questions probables, et qui répond

| Question | Qui | Réponse courte |
|---|---|---|
| Pourquoi des conteneurs et pas des VM ? | P1 | Démarrage sous la seconde, pas de noyau invité. L'énoncé le tranche. |
| Pourquoi SSH plutôt que l'API Docker ? | P1 | Qui parle au démon Docker est root sur la machine. Aucun port ouvert. |
| Où est stocké le mot de passe root ? | P2 | Chiffré Fernet, clé distincte, dans le Vault. Jamais dans un cookie. |
| Et si deux utilisateurs demandent le même port ? | P2 | Index unique partiel en base, limité aux machines actives. |
| La reprise rend-elle les fichiers ? | P3 | Non, et c'est assumé. Sans stockage partagé, seul le service repart. |
| Comment ajoute-t-on un worker ? | P3 | On l'allume. Aucune adresse n'est écrite côté contrôleur. |
| Pourquoi 147 tests sans Docker ? | P3 | `run_docker` est espionné : on vérifie la commande, pas le conteneur. |
| Combien de temps pour tout remonter ? | P1 | `vagrant up` puis un playbook. Tout est décrit en fichiers. |

---

## Ce qu'il ne faut pas faire

- **Ne pas lire ce script.** Le relire trois fois, puis parler.
- **Ne pas enchaîner les commandes en silence.** Pendant que ça charge, on
  explique ; c'est là que se gagne la note.
- **Ne pas promettre ce qu'on ne montrera pas.** Si le cluster n'est pas monté,
  le dire au moment du Test 6, avec la mesure à l'appui.
- **Ne pas dépasser.** Si on prend du retard, Personne 3 coupe les six
  scénarios d'intégration (11:30) et garde le Test 6 — c'est lui qui compte.

## Si quelque chose casse en direct

| Symptôme | Quoi faire, à voix haute |
|---|---|
| Le site ne répond pas | `docker compose -p insacloud ps`, puis `logs web --tail 20`. Commenter ce qu'on lit. |
| Le mode bureau n'apparaît pas | Les images `*_desktop` ne sont pas construites — et c'est volontaire : le catalogue ne propose pas ce qu'il ne peut pas livrer. |
| `port is already allocated` | Un conteneur d'un essai précédent traîne : `docker ps -a --filter "label=insacloud.role=rented-machine"`. |
| La machine met du temps | En parler : Docker publie le port avant que `sshd` écoute, c'est pour ça que nos tests réessaient au lieu d'attendre une durée fixe. |
