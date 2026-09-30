#!/usr/bin/env bash
# =============================================================================
#  tests/integration.sh - Les six tests d'intégration du cahier des charges
# -----------------------------------------------------------------------------
#  Ces tests portent sur une plateforme RÉELLEMENT DÉPLOYÉE, contrairement à
#  pytest qui vérifie le code sans rien lancer. Ils répondent à la question
#  « est-ce que l'ensemble fonctionne ensemble ? »
#
#      Test 1  Infrastructure   les nœuds existent et se parlent
#      Test 2  Configuration    Ansible a bien installé Docker et les services
#      Test 3  Application      inscription, connexion, tableau de bord
#      Test 4  Location         louer une machine et s'y connecter en SSH
#      Test 5  Expiration       une location échue est détruite
#      Test 6  Panne            un nœud tombe, la machine repart ailleurs
#
#  Usage :
#      ./tests/integration.sh https://192.168.56.10          (cluster Vagrant)
#      ./tests/integration.sh http://127.0.0.1:8088          (pile compose)
#      SKIP_PANNE=1 ./tests/integration.sh …                 (sans le test 6)
#
#  Le test 6 a besoin de pouvoir éteindre un nœud : il est ignoré si la
#  commande d'arrêt n'est pas fournie (ARRET_NOEUD / DEMARRAGE_NOEUD).
# =============================================================================
set -uo pipefail

BASE="${1:-https://127.0.0.1}"
CURL=(curl -ks --max-time 20)
COOKIES=$(mktemp)
COMPTE="integration_$$"
MOT_DE_PASSE="Integration-2026!"
reussis=0; echecs=0; ignores=0

trap 'rm -f "$COOKIES" /tmp/insacloud_rep.$$' EXIT

titre()   { printf '\n\033[1m%s\033[0m\n' "$1"; }
ok()      { printf '  \033[32m✔\033[0m %s\n' "$1"; reussis=$((reussis + 1)); }
ko()      { printf '  \033[31m✘\033[0m %s\n' "$1"; echecs=$((echecs + 1)); }
ignore()  { printf '  \033[33m–\033[0m %s\n' "$1"; ignores=$((ignores + 1)); }
verifier(){ if [ "$2" = "0" ]; then ok "$1"; else ko "$1"; fi; }

json() { python3 -c "
import json,sys
try: d = json.load(sys.stdin)
except Exception: print(''); sys.exit()
for cle in '$1'.split('.'):
    d = d.get(cle, '') if isinstance(d, dict) else ''
print(d)"; }

# =============================================================================
titre "Test 1 — Infrastructure : les nœuds sont là et se parlent"
# =============================================================================
sante=$("${CURL[@]}" "$BASE/health")
etat=$(echo "$sante" | json "status")
verifier "la plateforme répond (status=$etat)" "$([ "$etat" = "ok" ]; echo $?)"

total=$(echo "$sante" | json "workers_total")
dispo=$(echo "$sante" | json "workers_available")
verifier "au moins un nœud est enregistré ($total au total)" \
         "$([ "${total:-0}" -ge 1 ] 2>/dev/null; echo $?)"
verifier "au moins un nœud est disponible ($dispo disponible(s))" \
         "$([ "${dispo:-0}" -ge 1 ] 2>/dev/null; echo $?)"

parc=$("${CURL[@]}" "$BASE/workers")
joignables=$(echo "$parc" | python3 -c "
import json,sys
d=json.load(sys.stdin)
print(sum(1 for w in d.get('workers',[]) if w.get('reachable')))")
verifier "les nœuds envoient leur battement de cœur ($joignables joignable(s))" \
         "$([ "${joignables:-0}" -ge 1 ] 2>/dev/null; echo $?)"

# =============================================================================
titre "Test 2 — Configuration : Ansible a fait son travail"
# =============================================================================
base_ok=$(echo "$sante" | json "database")
verifier "la base de données répond (database=$base_ok)" \
         "$([ "$base_ok" = "ok" ]; echo $?)"

distros=$("${CURL[@]}" "$BASE/distributions" | json "count")
verifier "le catalogue des distributions est garni ($distros distribution(s))" \
         "$([ "${distros:-0}" -ge 1 ] 2>/dev/null; echo $?)"

entetes=$("${CURL[@]}" -D - -o /dev/null "$BASE/login")
verifier "nginx sert l'application avec une CSP stricte" \
         "$(echo "$entetes" | grep -qi "default-src 'self'"; echo $?)"
if [[ "$BASE" == https://* ]]; then
  verifier "HSTS est actif (TLS de bout en bout)" \
           "$(echo "$entetes" | grep -qi "strict-transport-security"; echo $?)"
else
  ignore "HSTS : la cible est en HTTP (pile locale)"
fi

# =============================================================================
titre "Test 3 — Application : inscription, connexion, tableau de bord"
# =============================================================================
inscription=$("${CURL[@]}" -b "$COOKIES" -c "$COOKIES" -X POST "$BASE/register" \
  -H 'Content-Type: application/json' \
  -d "{\"username\":\"$COMPTE\",\"email\":\"$COMPTE@insa-cvl.fr\",\"password\":\"$MOT_DE_PASSE\"}")
identifiant=$(echo "$inscription" | json "id")
verifier "création d'un compte (id=$identifiant)" \
         "$([ -n "$identifiant" ]; echo $?)"

"${CURL[@]}" -b "$COOKIES" -c "$COOKIES" -X POST "$BASE/logout" \
  -H 'Content-Type: application/json' -d '{}' >/dev/null
connexion=$("${CURL[@]}" -b "$COOKIES" -c "$COOKIES" -X POST "$BASE/login" \
  -H 'Content-Type: application/json' \
  -d "{\"username\":\"$COMPTE\",\"password\":\"$MOT_DE_PASSE\"}")
verifier "connexion avec le compte créé" \
         "$([ "$(echo "$connexion" | json 'username')" = "$COMPTE" ]; echo $?)"

code=$("${CURL[@]}" -o /dev/null -w '%{http_code}' -H 'Accept: application/json' "$BASE/instances")
verifier "les machines ne sont pas accessibles sans être connecté (HTTP $code)" \
         "$([ "$code" = "401" ]; echo $?)"

tableau=$("${CURL[@]}" -b "$COOKIES" -c "$COOKIES" "$BASE/dashboard")
verifier "le tableau de bord s'affiche pour l'utilisateur connecté" \
         "$(echo "$tableau" | grep -q "$COMPTE"; echo $?)"

# =============================================================================
titre "Test 4 — Location : louer une machine et s'y connecter"
# =============================================================================
distribution=$("${CURL[@]}" "$BASE/distributions" | python3 -c "
import json,sys
d=json.load(sys.stdin).get('distributions',[])
print(d[0]['name'] if d else '')")
location=$("${CURL[@]}" -b "$COOKIES" -c "$COOKIES" -X POST "$BASE/rent" \
  -H 'Content-Type: application/json' \
  -d "{\"distribution\":\"$distribution\",\"mode\":\"terminal\",\"duration\":5}")
echo "$location" > "/tmp/insacloud_rep.$$"
machine=$(echo "$location" | json "name")
noeud=$(echo "$location" | json "worker")
adresse=$(echo "$location" | json "worker_ip")
port=$(echo "$location" | json "ssh_port")
mot_de_passe=$(echo "$location" | json "root_password")
instance=$(echo "$location" | json "id")

verifier "la machine est créée ($machine sur $noeud)" "$([ -n "$machine" ]; echo $?)"
verifier "un port SSH est attribué ($port)" "$([ -n "$port" ]; echo $?)"
verifier "un mot de passe root est renvoyé, une seule fois" \
         "$([ ${#mot_de_passe} -eq 16 ]; echo $?)"

liste=$("${CURL[@]}" -b "$COOKIES" -c "$COOKIES" "$BASE/instances")
verifier "le mot de passe ne réapparaît jamais dans la liste" \
         "$(echo "$liste" | grep -q "root_password" && echo 1 || echo 0)"

if command -v sshpass >/dev/null 2>&1 && [ -n "$port" ]; then
  cible="${adresse:-127.0.0.1}"
  # Docker publie le port dès la création du conteneur, mais sshd met quelques
  # secondes à écouter derrière : attendre le port ne suffit pas, on réessaie
  # la connexion elle-même.
  #
  # PreferredAuthentications=password : sans cela, ssh propose d'abord les clés
  # de l'agent du poste et peut épuiser les tentatives avant d'arriver au mot
  # de passe — le test échouerait alors que la machine est saine.
  sortie=""
  for _ in $(seq 1 15); do
    sortie=$(sshpass -p "$mot_de_passe" ssh -o StrictHostKeyChecking=no \
             -o UserKnownHostsFile=/dev/null -o ConnectTimeout=10 \
             -o PreferredAuthentications=password -o PubkeyAuthentication=no \
             -o LogLevel=ERROR \
             -p "$port" "root@$cible" 'echo CONNECTE' 2>/dev/null)
    [ "$sortie" = "CONNECTE" ] && break
    sleep 2
  done
  verifier "connexion SSH réelle à la machine louée ($cible:$port)" \
           "$([ "$sortie" = "CONNECTE" ]; echo $?)"
else
  ignore "connexion SSH : sshpass n'est pas installé sur ce poste"
fi

# =============================================================================
titre "Test 5 — Expiration : une location échue est détruite"
# =============================================================================
courte=$("${CURL[@]}" -b "$COOKIES" -c "$COOKIES" -X POST "$BASE/rent" \
  -H 'Content-Type: application/json' \
  -d "{\"distribution\":\"$distribution\",\"mode\":\"terminal\",\"duration\":1}")
ephemere=$(echo "$courte" | json "id")
if [ -n "$ephemere" ]; then
  ok "location d'une minute créée (id=$ephemere)"
  echo "    attente de l'expiration puis du passage du Faucheur…"
  detruite=1
  for _ in $(seq 1 20); do
    sleep 10
    etat=$("${CURL[@]}" -b "$COOKIES" -c "$COOKIES" "$BASE/instances/$ephemere" | json "status")
    [ "$etat" = "expired" ] && { detruite=0; break; }
  done
  verifier "le Faucheur a détruit la machine expirée" "$detruite"
else
  ko "impossible de créer la location éphémère"
fi

# =============================================================================
titre "Test 6 — Panne : un nœud tombe, la machine repart ailleurs"
# =============================================================================
if [ "${SKIP_PANNE:-0}" = "1" ]; then
  ignore "test de panne désactivé (SKIP_PANNE=1)"
elif [ -z "${ARRET_NOEUD:-}" ]; then
  ignore "test de panne : définissez ARRET_NOEUD et DEMARRAGE_NOEUD"
  echo "      exemple : ARRET_NOEUD='vagrant halt %s' DEMARRAGE_NOEUD='vagrant up %s'"
elif [ "$noeud" = "local" ]; then
  ignore "test de panne : un seul nœud (mode mono-hôte), aucun repli possible"
else
  echo "    arrêt de $noeud…"
  # shellcheck disable=SC2059
  eval "$(printf "$ARRET_NOEUD" "$noeud")" >/dev/null 2>&1
  delai=$("${CURL[@]}" "$BASE/health" >/dev/null 2>&1; echo 200)
  echo "    attente de la détection puis de la reprise…"
  repris=1
  for _ in $(seq 1 24); do
    sleep 10
    apres=$("${CURL[@]}" -b "$COOKIES" -c "$COOKIES" "$BASE/instances/$instance")
    nouveau=$(echo "$apres" | json "worker")
    if [ -n "$nouveau" ] && [ "$nouveau" != "$noeud" ]; then
      ok "la machine est repartie : $noeud → $nouveau"
      repris=0
      verifier "la location est inchangée (statut ACTIVE)" \
               "$([ "$(echo "$apres" | json 'rental_status')" = "ACTIVE" ]; echo $?)"
      verifier "la reprise est comptabilisée" \
               "$([ "$(echo "$apres" | json 'migrations')" -ge 1 ] 2>/dev/null; echo $?)"
      break
    fi
  done
  [ "$repris" = "0" ] || ko "la machine n'a pas été reprise sur un autre nœud"
  if [ -n "${DEMARRAGE_NOEUD:-}" ]; then
    echo "    redémarrage de $noeud…"
    # shellcheck disable=SC2059
    eval "$(printf "$DEMARRAGE_NOEUD" "$noeud")" >/dev/null 2>&1
  fi
fi

# =============================================================================
printf '\n\033[1mBilan\033[0m : %d réussi(s), %d échec(s), %d ignoré(s)\n' \
       "$reussis" "$echecs" "$ignores"
[ "$echecs" -eq 0 ]
