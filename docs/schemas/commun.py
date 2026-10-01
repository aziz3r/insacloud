"""Briques communes aux quatre schémas de docs/.

Chaque schéma est décrit une seule fois ; la version claire et la version sombre
en sont dérivées. C'est ce qui empêche les deux variantes de diverger, et c'est
aussi ce qui permet de corriger un chiffre à un seul endroit.
"""
import pathlib

POLICE = "-apple-system, BlinkMacSystemFont, 'Segoe UI', Inter, Roboto, sans-serif"

CLAIR = dict(fond="#ffffff", panneau="#f7f8fa", boite="#ffffff", bord="#d8dce2",
             titre="#15171c", texte="#5b616b", discret="#8b9098", accent="#c8102e",
             pastille="#fdecef", vert="#0a7a3d", vert_fond="#eef7f1", vert_bord="#a8d5bc",
             alerte="#b45309", alerte_fond="#fef6e7", alerte_bord="#e8c98a")

SOMBRE = dict(fond="#0d1117", panneau="#161b22", boite="#11161d", bord="#30363d",
              titre="#e6edf3", texte="#9aa4b1", discret="#7d8590", accent="#ff6b81",
              pastille="#2a1218", vert="#3fb950", vert_fond="#122118", vert_bord="#2d5a3d",
              alerte="#e3a008", alerte_fond="#2b2213", alerte_bord="#5c4a1f")


def palettes(nom_clair, nom_sombre):
    """Associe chaque fichier de sortie à sa palette."""
    return {nom_clair: CLAIR, nom_sombre: SOMBRE}


def texte(x, y, contenu, taille=11.5, couleur="texte", gras=False, ancre="start", p=None):
    g = ' font-weight="600"' if gras else ""
    a = f' text-anchor="{ancre}"' if ancre != "start" else ""
    return (f'<text x="{x}" y="{y}" font-family="{POLICE}" font-size="{taille}"'
            f'{g} fill="{p[couleur]}"{a}>{contenu}</text>')


def boite(x, y, w, contenu, p, taille=11.5):
    return (f'<rect x="{x}" y="{y}" width="{w}" height="24" rx="6" fill="{p["boite"]}" '
            f'stroke="{p["bord"]}" stroke-width="1"/>'
            + texte(x + 10, y + 16, contenu, taille, "texte", p=p))


def panneau(x, y, w, h, p, fond="panneau", bord="bord"):
    return (f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="10" fill="{p[fond]}" '
            f'stroke="{p[bord]}" stroke-width="1.5"/>')


def fleche(d, p, pointille=False, couleur="discret", marqueur="arrow"):
    tirets = ' stroke-dasharray="5 4"' if pointille else ""
    return (f'<path d="{d}" fill="none" stroke="{p[couleur]}" stroke-width="1.6"'
            f'{tirets} marker-end="url(#{marqueur})"/>')


def marqueur(identifiant, couleur, p):
    return (f'<marker id="{identifiant}" viewBox="0 0 10 10" refX="9" refY="5" '
            f'markerWidth="6" markerHeight="6" orient="auto-start-reverse">'
            f'<path d="M 0 0 L 10 5 L 0 10 z" fill="{p[couleur]}"/></marker>')


def ecrire(nom, contenu):
    """Écrit dans docs/, quel que soit le répertoire courant."""
    (pathlib.Path(__file__).resolve().parent.parent / nom).write_text(contenu,
                                                                     encoding="utf-8")
    print(f"  ✔ {nom}")
