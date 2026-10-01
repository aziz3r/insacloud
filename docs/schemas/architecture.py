"""Génère les deux schémas d'architecture, clair et sombre, depuis une seule description."""
from commun import POLICE, boite, ecrire, fleche, marqueur, palettes, panneau, texte  # noqa: F401

PALETTES = palettes("architecture.svg", "architecture-dark.svg")

L, H = 900, 548


def construire(nom, p):
    s = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {L} {H}" width="{L}" '
         f'height="{H}" role="img" aria-label="Architecture InsaCloud : un contrôleur '
         f'et trois nœuds d\'exécution qui s\'inscrivent eux-mêmes">']
    s.append('<defs>'
             f'<marker id="arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" '
             f'markerHeight="6" orient="auto-start-reverse">'
             f'<path d="M 0 0 L 10 5 L 0 10 z" fill="{p["discret"]}"/></marker>'
             f'<marker id="arrowV" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" '
             f'markerHeight="6" orient="auto-start-reverse">'
             f'<path d="M 0 0 L 10 5 L 0 10 z" fill="{p["vert"]}"/></marker>'
             '</defs>')
    s.append(f'<rect width="{L}" height="{H}" rx="12" fill="{p["fond"]}"/>')

    # --- Navigateur ----------------------------------------------------------
    s.append(panneau(18, 196, 126, 76, p))
    s.append(texte(81, 228, "Navigateur", 13, "titre", gras=True, ancre="middle", p=p))
    s.append(texte(81, 246, "utilisateur", 11, "discret", ancre="middle", p=p))
    s.append(fleche("M 148 234 L 196 234", p))
    s.append(texte(172, 224, "HTTPS", 11, "discret", ancre="middle", p=p))

    # --- Contrôleur ----------------------------------------------------------
    s.append(panneau(200, 34, 252, 330, p))
    s.append(texte(216, 60, "controller — 192.168.56.10", 14, "accent", gras=True, p=p))
    services = ["nginx · TLS 1.3, HSTS", "Gunicorn · 4 workers",
                "Flask · API et interface", "Ordonnanceur · nœud le moins chargé",
                "Faucheur · expirations et reprise", "Watchdog · timer 1 min",
                "PostgreSQL ou SQLite · Vault"]
    for i, libelle in enumerate(services):
        s.append(boite(214, 74 + i * 30, 224, libelle, p, taille=11))

    # --- Trois workers -------------------------------------------------------
    for i, (nom_w, ip) in enumerate([("worker1", ".11"), ("worker2", ".12"), ("worker3", ".13")]):
        y = 34 + i * 112
        s.append(panneau(576, y, 306, 96, p))
        s.append(texte(592, y + 26, f"{nom_w} — 192.168.56{ip}", 13.5, "titre", gras=True, p=p))
        s.append(boite(590, y + 38, 278, "dockerd · live-restore", p, taille=11))
        s.append(boite(590, y + 66, 278, "agent · machines louées", p, taille=11))

    # --- Pilotage : le contrôleur agit sur chaque nœud -----------------------
    s.append(fleche("M 456 110 C 508 110, 520 82, 572 82", p))
    s.append(texte(514, 72, "docker -H ssh://", 11, "discret", ancre="middle", p=p))
    s.append(fleche("M 456 170 C 508 170, 520 194, 572 194", p))
    s.append(fleche("M 456 230 C 508 230, 520 306, 572 306", p))

    # --- Battement de cœur : un seul tracé, sous les nœuds, sans croisement --
    s.append(fleche("M 729 362 L 729 404 L 326 404 L 326 368", p,
                    couleur="vert", marqueur="arrowV"))
    s.append(texte(528, 398, "register · heartbeat", 11, "vert", ancre="middle", p=p))

    # --- Accès direct de l'utilisateur à sa machine --------------------------
    s.append(fleche("M 81 276 L 81 450 L 800 450 L 800 362", p, pointille=True))
    s.append(texte(430, 472, "SSH · terminal web · bureau (ports 8000-9000)", 11,
                   "discret", ancre="middle", p=p))

    # --- Légende -------------------------------------------------------------
    s.append(texte(18, 508, "Le contrôleur n'exécute aucune machine louée : il n'a que le client "
                            "Docker. Chaque worker s'inscrit seul au démarrage, puis", 11,
                   "discret", p=p))
    s.append(texte(18, 526, "signale sa présence. Sans battement de cœur il passe hors ligne, et "
                            "ses machines sont recréées sur un nœud disponible.", 11,
                   "discret", p=p))
    s.append("</svg>")
    return "\n".join(s)


for nom, palette in PALETTES.items():
    ecrire(nom, construire(nom, palette))
