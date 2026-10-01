"""Génère le schéma du modèle de données, clair et sombre."""
from commun import ecrire, palettes, panneau, texte

PALETTES = palettes("base-de-donnees.svg", "base-de-donnees-dark.svg")

L = 900
COLONNES = (28, 320, 612)
LARGEUR = 260

ENTETE, LIGNE, MARGE, NOTE = 32, 18, 12, 17

# Chaque table : nom, colonnes, note de bas de table, colonne d'affichage.
TABLES = [
    ("users", ["id · PK", "username · unique", "email · unique", "password_hash",
               "ssh_public_key", "created_at"], None, 0),
    ("login_attempts", ["id · PK", "username · ip", "success", "created_at"],
     ["index (created_at, username, ip)", "anti-force-brute"], 0),
    ("instances", ["id · PK", "container_id · container_name", "worker_id · FK",
                   "distribution_id · FK", "ssh_port · term_port · gui_port",
                   "root_password — chiffré (Fernet)", "mode · status", "migrations",
                   "created_at · terminated_at"], None, 1),
    ("distributions", ["id · PK", "name · unique", "docker_image · version", "status",
                       "label · hint"], ["ubuntu · debian · alpine"], 1),
    ("workers", ["id · PK", "hostname · unique", "ip", "status", "cpu · memory · capacity",
                 "last_heartbeat · registered_at"],
     ["status : AVAILABLE · BUSY · OFFLINE", "tenu à jour par l'agent du nœud"], 2),
    ("rentals", ["id · PK", "user_id · FK", "instance_id · FK", "start_time · end_time",
                 "status"], ["status : ACTIVE · EXPIRED · STOPPED"], 2),
]

RELATIONS = [
    ("users  1 — n  rentals", "un utilisateur loue plusieurs fois"),
    ("rentals  1 — 1  instances", "la location survit au conteneur : c'est ce qui permet "
                                  "de recréer la machine ailleurs"),
    ("instances  n — 1  workers", "une machine s'exécute sur un nœud à la fois"),
    ("instances  n — 1  distributions", "le catalogue est en base, pas en dur"),
]

NOTES = [
    "Index unique partiel sur (worker_id, ssh_port), limité aux instances actives : "
    "un port redevient libre dès que la machine est détruite.",
    "Index (status, end_time) sur rentals : c'est exactement la requête que le Faucheur "
    "exécute toutes les dix secondes.",
    "Schéma décrit en SQLAlchemy et versionné par Alembic · SQLite en mode WAL, "
    "ou PostgreSQL via DATABASE_URL.",
]


def hauteur(table):
    _, colonnes, notes, _ = table
    return ENTETE + len(colonnes) * LIGNE + MARGE + len(notes or ()) * NOTE


def dessiner(x, y, table, p):
    nom, colonnes, notes, _ = table
    s = [panneau(x, y, LARGEUR, hauteur(table), p),
         f'<rect x="{x}" y="{y}" width="{LARGEUR}" height="{ENTETE}" rx="10" '
         f'fill="{p["pastille"]}"/>',
         f'<rect x="{x}" y="{y + ENTETE - 10}" width="{LARGEUR}" height="10" '
         f'fill="{p["pastille"]}"/>',
         f'<line x1="{x}" y1="{y + ENTETE}" x2="{x + LARGEUR}" y2="{y + ENTETE}" '
         f'stroke="{p["bord"]}" stroke-width="1.5"/>',
         texte(x + 14, y + 21, nom, 12.5, "accent", gras=True, p=p)]
    for i, colonne in enumerate(colonnes):
        s.append(texte(x + 14, y + ENTETE + 14 + i * LIGNE, colonne, 10.5, "texte", p=p))
    for i, note in enumerate(notes or ()):
        s.append(texte(x + 14, y + ENTETE + len(colonnes) * LIGNE + 12 + i * NOTE, note,
                       9.5, "discret", p=p))
    return s


def construire(nom_fichier, p):
    # Empilement par colonne, pour une page équilibrée.
    y_colonne, places = [76, 76, 76], []
    for table in TABLES:
        c = table[3]
        places.append((COLONNES[c], y_colonne[c], table))
        y_colonne[c] += hauteur(table) + 20

    bas = max(y_colonne) + 8
    h_relations = 30 + len(RELATIONS) * 19 + 12
    h = bas + h_relations + 16 + len(NOTES) * 17 + 18

    s = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {L} {h}" width="{L}" '
         f'height="{h}" role="img" aria-label="Modèle de données InsaCloud : six tables, '
         f'dont Instance et Rental séparées pour permettre la reprise sur un autre nœud">',
         f'<rect width="{L}" height="{h}" rx="12" fill="{p["fond"]}"/>',
         texte(28, 40, "InsaCloud — modèle de données", 15, "titre", gras=True, p=p),
         texte(28, 60, "Six tables. Instance décrit un conteneur, Rental l'engagement de "
                       "location : les séparer permet de recréer la machine ailleurs sans "
                       "toucher à la location.", 10.5, "discret", p=p)]
    for x, y, table in places:
        s.extend(dessiner(x, y, table, p))

    s.append(panneau(28, bas, 844, h_relations, p))
    s.append(texte(44, bas + 22, "Relations", 12, "titre", gras=True, p=p))
    for i, (relation, sens) in enumerate(RELATIONS):
        y = bas + 42 + i * 19
        s.append(texte(44, y, relation, 10.5, "accent", gras=True, p=p))
        s.append(texte(236, y, sens, 10.5, "texte", p=p))

    for i, ligne in enumerate(NOTES):
        s.append(texte(28, bas + h_relations + 28 + i * 17, ligne, 10.5, "texte", p=p))
    s.append("</svg>")
    return "\n".join(s)


for nom, palette in PALETTES.items():
    ecrire(nom, construire(nom, palette))
