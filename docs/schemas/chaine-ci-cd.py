"""Génère le schéma de la chaîne d'intégration et de déploiement, clair et sombre."""
from commun import ecrire, fleche, marqueur, palettes, panneau, texte

PALETTES = palettes("chaine-ci-cd.svg", "chaine-ci-cd-dark.svg")

L = 900

# Trois étages par rangée, alignés sur les travaux réels de .github/workflows/ci.yml.
X, LARGEUR, ECART = 174, 206, 40
RANGEE_1, RANGEE_2 = 88, 358
H_ETAGE = 168

ETAGES_1 = [
    ("1", "Qualité", ["ruff — style, imports morts,", "      motifs dangereux",
                      "pytest — 94 tests,", "      couverture conservée",
                      "ansible-lint — profil", "      « production »"]),
    ("2", "Sécurité", ["bandit — SAST", "pip-audit — SCA", "gitleaks — secrets,",
                       "      historique compris", "nuclei + dast.sh — DAST",
                       "      sur l'app en marche"]),
    ("3", "Intégration", ["la pile compose est montée", "en vrai, puis parcourue :",
                          "inscription, location, SSH,", "expiration, reprise après",
                          "panne d'un nœud"]),
]

ETAGES_2 = [
    ("4", "Images", ["hadolint — 4 Dockerfiles", "les images sont construites",
                     "Trivy — aucune faille", "      corrigeable HIGH", "      ou CRITICAL ne passe"]),
    ("5", "Publication", ["ghcr.io", "étiquettes :", "      version · commit · latest",
                          "SBOM (Syft) conservé", "      en artefact"]),
    ("6", "Déploiement", ["ansible-playbook deploy.yml", "sur le contrôleur", "puis GET /health :",
                          "      un déploiement qui ne", "      sert pas fait échouer"]),
]

H = RANGEE_2 + H_ETAGE + 28 + 3 * 17 + 14

NOTES = [
    "Les deux derniers travaux ne s'exécutent que sur un envoi vers « main », et seulement "
    "si les neuf précédents sont au vert.",
    "Le déploiement s'annonce ignoré, plutôt que de faire croire à un déploiement, si "
    "les secrets DEPLOY_* ou ANSIBLE_VAULT_PASSWORD manquent.",
    "Chaque commande de la chaîne est rejouable telle quelle sur un poste : rien n'y est "
    "spécifique à l'agent d'intégration.",
]


def etage(x, y, numero, titre, lignes, p):
    s = [panneau(x, y, LARGEUR, H_ETAGE, p),
         f'<circle cx="{x + 24}" cy="{y + 26}" r="11" fill="{p["pastille"]}" '
         f'stroke="{p["accent"]}" stroke-width="1.2"/>',
         texte(x + 24, y + 30, numero, 11.5, "accent", gras=True, ancre="middle", p=p),
         texte(x + 44, y + 30, titre, 12.5, "titre", gras=True, p=p)]
    for i, ligne in enumerate(lignes):
        # Une ligne de continuation s'écrit avec des espaces en tête ; SVG les
        # ignore, on la décale donc explicitement.
        retrait = 14 if ligne.startswith(" ") else 0
        s.append(texte(x + 18 + retrait, y + 56 + i * 18, ligne.strip(), 10.5, "texte", p=p))
    return s


def construire(nom_fichier, p):
    s = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {L} {H}" width="{L}" '
         f'height="{H}" role="img" aria-label="Chaîne d\'intégration et de déploiement '
         f'continus : onze travaux GitHub Actions, du style au déploiement vérifié">',
         '<defs>' + marqueur("arrow", "discret", p) + marqueur("arrowA", "accent", p)
         + '</defs>',
         f'<rect width="{L}" height="{H}" rx="12" fill="{p["fond"]}"/>',
         texte(28, 40, "InsaCloud — intégration et déploiement continus", 15, "titre",
               gras=True, p=p),
         texte(28, 60, "Onze travaux GitHub Actions, déclenchés à chaque envoi. Aucune "
                       "image n'atteint le registre sans avoir franchi tous les contrôles.",
               10.5, "discret", p=p)]

    # Déclencheur, aligné sur le centre de la première rangée.
    s.append(panneau(28, RANGEE_1 + 56, 118, 56, p))
    s.append(texte(87, RANGEE_1 + 84, "git push", 12.5, "titre", gras=True,
                   ancre="middle", p=p))
    s.append(texte(87, RANGEE_1 + 102, "ou pull request", 10.5, "discret",
                   ancre="middle", p=p))
    s.append(fleche(f"M 150 {RANGEE_1 + 84} L 170 {RANGEE_1 + 84}", p))

    for rangee, etages in ((RANGEE_1, ETAGES_1), (RANGEE_2, ETAGES_2)):
        for i, (numero, titre, lignes) in enumerate(etages):
            x = X + i * (LARGEUR + ECART)
            s.extend(etage(x, rangee, numero, titre, lignes, p))
            if i < 2:
                s.append(fleche(f"M {x + LARGEUR + 4} {rangee + 84} "
                                f"L {x + LARGEUR + ECART - 4} {rangee + 84}", p))

    # Le verrou : rien ne part tant que les neuf premiers travaux ne sont pas au vert.
    verrou = RANGEE_1 + H_ETAGE + 24
    s.append(panneau(174, verrou, 698, 42, p, fond="pastille", bord="accent"))
    s.append(texte(523, verrou + 26, "Verrou — publication et déploiement n'ont lieu que "
                                     "si tout ce qui précède est au vert", 11.5, "accent",
                   gras=True, ancre="middle", p=p))
    s.append(fleche(f"M 790 {RANGEE_1 + H_ETAGE + 2} L 790 {verrou - 4}", p,
                    couleur="accent", marqueur="arrowA"))
    s.append(fleche(f"M 256 {verrou + 42 + 2} L 256 {RANGEE_2 - 4}", p,
                    couleur="accent", marqueur="arrowA"))

    for i, ligne in enumerate(NOTES):
        s.append(texte(28, RANGEE_2 + H_ETAGE + 28 + i * 17, ligne, 10.5, "texte", p=p))
    s.append("</svg>")
    return "\n".join(s)


for nom, palette in PALETTES.items():
    ecrire(nom, construire(nom, palette))
