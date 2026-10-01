"""Génère le schéma du cycle de vie d'une machine, clair et sombre."""
from commun import POLICE, ecrire, palettes, texte  # noqa: F401

PALETTES = palettes("cycle-de-vie.svg", "cycle-de-vie-dark.svg")

L, H = 900, 416


def etape(x, numero, titre_etape, lignes, p):
    """Une étape du cycle : pastille numérotée, titre, trois détails."""
    s = [f'<rect x="{x}" y="66" width="152" height="128" rx="10" fill="{p["panneau"]}" '
         f'stroke="{p["bord"]}" stroke-width="1.5"/>',
         f'<circle cx="{x + 22}" cy="90" r="11" fill="{p["pastille"]}" '
         f'stroke="{p["accent"]}" stroke-width="1.2"/>',
         texte(x + 22, 94, numero, 11.5, "accent", gras=True, ancre="middle", p=p),
         texte(x + 42, 94, titre_etape, 12.5, "titre", gras=True, p=p)]
    for i, ligne in enumerate(lignes):
        s.append(texte(x + 16, 120 + i * 19, ligne, 11, "texte", p=p))
    return s


def construire(nom, p):
    s = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {L} {H}" width="{L}" '
         f'height="{H}" role="img" aria-label="Cycle de vie d\'une machine louée, '
         f'de la demande à l\'expiration, avec la reprise après panne d\'un nœud">']
    s.append(f'<defs><marker id="fl" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" '
             f'markerHeight="6" orient="auto-start-reverse">'
             f'<path d="M 0 0 L 10 5 L 0 10 z" fill="{p["discret"]}"/></marker>'
             f'<marker id="flA" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" '
             f'markerHeight="6" orient="auto-start-reverse">'
             f'<path d="M 0 0 L 10 5 L 0 10 z" fill="{p["alerte"]}"/></marker></defs>')
    s.append(f'<rect width="{L}" height="{H}" rx="12" fill="{p["fond"]}"/>')
    s.append(texte(28, 40, "Cycle de vie d'une machine louée", 15, "titre", gras=True, p=p))

    etapes = [
        ("1", "Choix", ["distribution", "terminal ou bureau", "durée 1-120 min"]),
        ("2", "Placement", ["nœud joignable", "le moins chargé", "ports libres"]),
        ("3", "Démarrage", ["docker run -d", "--restart=always", "supervisord PID 1"]),
        ("4", "Accès", ["SSH par clé ou", "mot de passe", "terminal · bureau"]),
        ("5", "Expiration", ["le Faucheur lance", "docker rm -f", "+ réconciliation"]),
    ]
    for i, (num, titre_etape, lignes) in enumerate(etapes):
        x = 28 + i * 172
        s.extend(etape(x, num, titre_etape, lignes, p))
        if i < len(etapes) - 1:
            s.append(f'<path d="M {x + 154} 130 L {x + 168} 130" fill="none" '
                     f'stroke="{p["discret"]}" stroke-width="1.6" marker-end="url(#fl)"/>')

    # --- Branche de reprise : le nœud tombe en cours de location -------------
    s.append(f'<rect x="200" y="226" width="472" height="76" rx="10" '
             f'fill="{p["alerte_fond"]}" stroke="{p["alerte_bord"]}" stroke-width="1.5"/>')
    s.append(texte(216, 248, "Si le nœud tombe pendant la location", 12.5, "alerte",
                   gras=True, p=p))
    s.append(texte(216, 269, "plus de battement de cœur  →  nœud OFFLINE", 11, "alerte", p=p))
    s.append(texte(216, 288, "→  machine recréée sur un nœud disponible  →  retour "
                             "à l'étape 4", 11, "alerte", p=p))
    # Depuis l'étape « Accès » vers la branche, puis retour vers « Accès »
    s.append(f'<path d="M 620 198 L 620 222" fill="none" stroke="{p["alerte"]}" '
             f'stroke-width="1.6" stroke-dasharray="4 3" marker-end="url(#flA)"/>')
    s.append(f'<path d="M 240 226 L 240 210 L 560 210 L 560 198" fill="none" '
             f'stroke="{p["alerte"]}" stroke-width="1.6" stroke-dasharray="4 3" '
             f'marker-end="url(#flA)"/>')
    s.append(texte(688, 258, "la location", 10.5, "alerte", p=p))
    s.append(texte(688, 273, "n'est pas", 10.5, "alerte", p=p))
    s.append(texte(688, 288, "interrompue", 10.5, "alerte", p=p))

    # --- Ce qui vaut tout au long du cycle -----------------------------------
    s.append(texte(28, 334, "Tout au long du cycle", 12, "titre", gras=True, p=p))
    for i, ligne in enumerate([
        "Mot de passe affiché une seule fois, puis conservé chiffré (Fernet) et réaffiché "
        "après re-authentification — jamais dans un cookie.",
        "Un service qui tombe est relancé par supervisord ; un nœud qui redémarre retrouve "
        "ses machines et leurs données.",
        "Un nœud perdu, en revanche, ne rend pas les fichiers : la machine repart neuve "
        "ailleurs, avec un nouveau port et un nouveau mot de passe.",
    ]):
        s.append(texte(28, 354 + i * 17, ligne, 10.5, "texte", p=p))
    s.append("</svg>")
    return "\n".join(s)


for nom, palette in PALETTES.items():
    ecrire(nom, construire(nom, palette))
