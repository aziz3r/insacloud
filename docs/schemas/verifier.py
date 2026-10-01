"""Vérifie qu'aucun texte ne déborde de son schéma.

Un débordement ne casse rien : il passe inaperçu dans le code et se voit
seulement à l'œil, sur le rendu. Ce contrôle estime la largeur de chaque
<text> à partir des chasses d'Helvetica — assez proche de la police système
pour attraper un vrai débordement — et refuse qu'un texte sorte de la toile.
"""
import pathlib
import re
import sys
import xml.etree.ElementTree as ET

MARGE = 14       # marge minimale attendue au bord de la toile
RESPIRATION = 6  # marge minimale attendue au bord d'un panneau
# La police système rend un peu plus large qu'Helvetica : calibré sur un rendu réel.
ELARGISSEMENT = 1.05

# Chasses d'Helvetica, en millièmes de cadratin.
CHASSES = {" ": 278, "!": 278, '"': 355, "#": 556, "$": 556, "%": 889, "&": 667,
           "'": 191, "(": 333, ")": 333, "*": 389, "+": 584, ",": 278, "-": 333,
           ".": 278, "/": 278, ":": 278, ";": 278, "<": 584, "=": 584, ">": 584,
           "?": 556, "@": 1015, "[": 278, "\\": 278, "]": 278, "^": 469, "_": 556,
           "`": 333, "{": 334, "|": 260, "}": 334, "~": 584}
CHASSES.update({c: 556 for c in "0123456789"})
CHASSES.update({c: 556 for c in "abcdeghknopqsuvxyzâàäéèêëîïôöûùüç"})
CHASSES.update({c: 222 for c in "ilïî"})
CHASSES.update({c: 278 for c in "jft"})
CHASSES.update({c: 333 for c in "r"})
CHASSES.update({c: 500 for c in "w"})
CHASSES.update({c: 889 for c in "m"})
CHASSES.update({c: 1000 for c in "œæ"})
CHASSES.update({c: 667 for c in "ABCDEGHKNOPQRSUVXYZÀÂÄÉÈÊËÎÏÔÖÛÙÜÇ"})
CHASSES.update({c: 278 for c in "I"})
CHASSES.update({c: 611 for c in "FTL"})
CHASSES.update({c: 944 for c in "MW"})
CHASSES.update({c: 1000 for c in "Œ"})
CHASSES.update({"—": 1000, "–": 556, "·": 278, "→": 1000, "«": 556, "»": 556,
                "’": 191, "“": 333, "”": 333, "🔒": 1000})

GRAS = 1.06  # le demi-gras utilisé ici élargit un peu les lettres


def largeur(contenu, taille, gras):
    total = sum(CHASSES.get(c, 556) for c in contenu)
    return total / 1000 * taille * (GRAS if gras else 1) * ELARGISSEMENT


def contenant(rectangles, x, y, toile):
    """Le plus petit rectangle qui entoure le point d'ancrage du texte.

    Sans lui, un texte peut déborder de son panneau tout en restant dans la
    toile — exactement le défaut que ce contrôle doit attraper.
    """
    candidats = [r for r in rectangles
                 if r[0] <= x <= r[0] + r[2] and r[1] <= y <= r[1] + r[3]
                 and r[2] < toile - 2 * MARGE]
    if not candidats:
        return None
    return min(candidats, key=lambda r: r[2] * r[3])


def controler(chemin):
    racine = ET.parse(chemin).getroot()
    bornes = racine.get("viewBox").split()
    toile = float(bornes[2])
    rectangles = [(float(r.get("x", 0)), float(r.get("y", 0)),
                   float(r.get("width", 0)), float(r.get("height", 0)))
                  for r in racine.iter("{http://www.w3.org/2000/svg}rect")]
    fautes = []
    for noeud in racine.iter("{http://www.w3.org/2000/svg}text"):
        contenu = "".join(noeud.itertext())
        if not contenu.strip():
            continue
        taille = float(noeud.get("font-size", 12))
        w = largeur(contenu, taille, noeud.get("font-weight") == "600")
        x = float(noeud.get("x", 0))
        ancre = noeud.get("text-anchor", "start")
        gauche = x - w / 2 if ancre == "middle" else (x - w if ancre == "end" else x)
        droite = gauche + w
        y = float(noeud.get("y", 0))
        boite = contenant(rectangles, x, y - taille * 0.3, toile)
        if boite is None:
            mini, maxi, cadre = MARGE, toile - MARGE, f"la toile ({toile:.0f})"
        else:
            mini = boite[0] + RESPIRATION
            maxi = boite[0] + boite[2] - RESPIRATION
            cadre = f"son panneau ({boite[0]:.0f} → {boite[0] + boite[2]:.0f})"
        if gauche < mini or droite > maxi:
            fautes.append(f"      {contenu[:68]!r}\n"
                          f"        occupe {gauche:.0f} → {droite:.0f}, "
                          f"hors de {cadre}")
    return fautes


def principal():
    docs = pathlib.Path(__file__).resolve().parent.parent
    total = 0
    for chemin in sorted(docs.glob("*.svg")):
        fautes = controler(chemin)
        total += len(fautes)
        etat = "OK" if not fautes else f"{len(fautes)} débordement(s)"
        print(f"  {chemin.name:<30} {etat}")
        for faute in fautes:
            print(faute)
    if total:
        print(f"\n  {total} texte(s) hors de la toile.")
    return 1 if total else 0


if __name__ == "__main__":
    sys.exit(principal())
