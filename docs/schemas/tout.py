"""Régénère les quatre schémas, puis vérifie qu'aucun texte ne déborde.

À lancer après toute retouche d'un générateur :

    python3 docs/schemas/tout.py

L'intégration continue le relance et refuse un envoi où les SVG ne
correspondraient plus à leur description : c'est ce qui empêche un schéma de
vieillir en silence pendant que le projet avance.
"""
import pathlib
import runpy
import sys

import verifier

GENERATEURS = ["architecture.py", "cycle-de-vie.py", "base-de-donnees.py",
               "chaine-ci-cd.py"]

ici = pathlib.Path(__file__).resolve().parent
for generateur in GENERATEURS:
    runpy.run_path(str(ici / generateur), run_name="__main__")

print()
sys.exit(verifier.principal())
