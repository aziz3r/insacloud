#!/usr/bin/env python3
"""
verifier-workflow.py - Vérifie la syntaxe shell de chaque étape du workflow.

Une erreur de syntaxe dans un bloc « run: » ne se voit qu'au moment où
l'étape s'exécute — parfois au bout de dix minutes de chaîne, et seulement
sur la branche principale si l'étape est conditionnelle. Deux guillemets
déséquilibrées ont ainsi fait échouer deux exécutions de suite.

Ce script extrait chaque bloc, neutralise les expressions ${{ }} propres à
GitHub, et le passe à « bash -n ». Il tourne en quelques secondes, en tête
de chaîne, et se rejoue à l'identique sur un poste :

    python3 .github/verifier-workflow.py
"""

import os
import re
import subprocess  # nosec B404 - bash -n est précisément l'outil voulu
import sys
import tempfile
from pathlib import Path

import yaml

RACINE = Path(__file__).resolve().parent


def verifier(chemin: Path) -> int:
    """Retourne le nombre d'étapes en erreur dans ce fichier de workflow."""
    description = yaml.safe_load(chemin.read_text(encoding="utf-8"))
    erreurs = 0
    etapes = 0

    for nom_travail, travail in (description.get("jobs") or {}).items():
        for index, etape in enumerate(travail.get("steps") or [], start=1):
            script = etape.get("run")
            if not script:
                continue
            etapes += 1
            # ${{ ... }} n'est pas du shell : GitHub le remplace avant exécution.
            neutralise = re.sub(r"\$\{\{[^}]*\}\}", "JETON", script)
            with tempfile.NamedTemporaryFile("w", suffix=".sh", delete=False) as fichier:
                fichier.write("#!/bin/bash\n" + neutralise)
                temporaire = fichier.name
            resultat = subprocess.run(  # nosec B603  # noqa: S603
                ["/bin/bash", "-n", temporaire],
                capture_output=True, text=True, check=False)
            os.unlink(temporaire)
            if resultat.returncode != 0:
                erreurs += 1
                libelle = etape.get("name") or f"étape {index}"
                print(f"{chemin.name} : {nom_travail} / « {libelle} »", file=sys.stderr)
                for ligne in resultat.stderr.strip().splitlines()[-2:]:
                    print(f"    {ligne}", file=sys.stderr)

    print(f"{chemin.name} : {etapes} étapes shell vérifiées, {erreurs} erreur(s)")
    return erreurs


def main() -> int:
    fichiers = sorted((RACINE / "workflows").glob("*.yml"))
    if not fichiers:
        print("Aucun workflow trouvé.", file=sys.stderr)
        return 1
    return 1 if sum(verifier(f) for f in fichiers) else 0


if __name__ == "__main__":
    sys.exit(main())
