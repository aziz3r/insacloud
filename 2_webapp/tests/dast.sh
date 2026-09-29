#!/usr/bin/env bash
# =============================================================================
#  tests/dast.sh - Contrôles dynamiques sur l'application EN FONCTIONNEMENT
# -----------------------------------------------------------------------------
#  Les tests pytest vérifient le code ; ce script vérifie le service tel qu'il
#  répond réellement sur le réseau : en-têtes de sécurité, pages protégées,
#  jeton CSRF exigé, attributs des cookies, absence de fuite d'information.
#
#  Usage :  ./tests/dast.sh [URL]        (défaut : http://127.0.0.1:5000)
#  Sortie :  0 si tout passe, 1 sinon (utilisé tel quel par l'intégration continue)
# =============================================================================
set -uo pipefail
BASE="${1:-http://127.0.0.1:5000}"
CURL=(curl -ks --max-time 10)
reussis=0; echecs=0

verifier() {   # $1 = description, $2 = 0/1 attendu du test, ici passé déjà évalué
    if [ "$2" = "0" ]; then
        printf '  \033[32mOK\033[0m   %s\n' "$1"; reussis=$((reussis + 1))
    else
        printf '  \033[31mKO\033[0m   %s\n' "$1"; echecs=$((echecs + 1))
    fi
}

contient() { echo "$1" | grep -qi -- "$2"; }

echo "Contrôles dynamiques sur $BASE"

# --- Disponibilité ------------------------------------------------------------
code=$("${CURL[@]}" -o /dev/null -w '%{http_code}' "$BASE/login")
verifier "la page de connexion répond (HTTP $code)" "$([ "$code" = "200" ]; echo $?)"

entetes=$("${CURL[@]}" -D - -o /dev/null "$BASE/login")

# --- En-têtes de sécurité -----------------------------------------------------
for attendu in \
    "content-security-policy:default-src 'self'" \
    "content-security-policy:frame-ancestors 'none'" \
    "x-content-type-options:nosniff" \
    "x-frame-options:DENY" \
    "referrer-policy:no-referrer" \
    "cache-control:no-store" \
    "cross-origin-opener-policy:same-origin"
do
    entete="${attendu%%:*}"; valeur="${attendu#*:}"
    verifier "en-tête $entete contient « $valeur »" \
             "$(contient "$entetes" "$valeur"; echo $?)"
done

# La CSP ne doit tolérer ni script ni style en ligne : sinon une injection s'exécute.
verifier "la CSP n'autorise ni 'unsafe-inline' ni 'unsafe-eval'" \
         "$(contient "$entetes" "unsafe-" && echo 1 || echo 0)"

# --- Absence de fuite d'information ------------------------------------------
verifier "aucune version de serveur divulguée (en-tête Server)" \
         "$(echo "$entetes" | grep -i '^server:' | grep -qE '[0-9]+\.[0-9]+' && echo 1 || echo 0)"
verifier "aucun en-tête X-Powered-By" \
         "$(contient "$entetes" "x-powered-by" && echo 1 || echo 0)"

# --- Pages protégées ----------------------------------------------------------
redirection=$("${CURL[@]}" -o /dev/null -w '%{http_code} %{redirect_url}' "$BASE/dashboard")
verifier "/dashboard renvoie un anonyme vers la connexion ($redirection)" \
         "$(contient "$redirection" "302" && contient "$redirection" "/login"; echo $?)"

# --- Protection CSRF ----------------------------------------------------------
sanscsrf=$("${CURL[@]}" -o /dev/null -w '%{http_code}' \
           -d "username=pirate&password=MotDePasse123456" "$BASE/register")
verifier "une inscription sans jeton CSRF est refusée (HTTP $sanscsrf)" \
         "$([ "$sanscsrf" != "200" ] && [ "$sanscsrf" != "201" ]; echo $?)"

# --- Attributs du cookie de session ------------------------------------------
cookie=$("${CURL[@]}" -D - -o /dev/null "$BASE/login" | grep -i '^set-cookie:')
if [ -n "$cookie" ]; then
    verifier "le cookie de session est HttpOnly" "$(contient "$cookie" "httponly"; echo $?)"
    verifier "le cookie de session est SameSite=Strict" "$(contient "$cookie" "samesite=strict"; echo $?)"
else
    echo "  --   aucun cookie posé sur /login (session créée à la connexion)"
fi

# --- Méthodes HTTP ------------------------------------------------------------
trace=$("${CURL[@]}" -o /dev/null -w '%{http_code}' -X TRACE "$BASE/login")
verifier "la méthode TRACE est refusée (HTTP $trace)" \
         "$([ "$trace" != "200" ]; echo $?)"
deconnexion=$("${CURL[@]}" -o /dev/null -w '%{http_code}' "$BASE/logout")
verifier "la déconnexion n'est pas possible en GET (HTTP $deconnexion)" \
         "$([ "$deconnexion" = "405" ]; echo $?)"

# --- Taille des requêtes ------------------------------------------------------
gros=$(head -c 40000 /dev/zero | tr '\0' 'a')
trop=$("${CURL[@]}" -o /dev/null -w '%{http_code}' -d "username=$gros" "$BASE/register")
verifier "une requête de 40 Ko est rejetée (HTTP $trop)" \
         "$([ "$trop" = "413" ] || [ "$trop" = "403" ]; echo $?)"

echo
echo "Bilan : $reussis contrôle(s) réussi(s), $echecs échec(s)"
[ "$echecs" -eq 0 ]
