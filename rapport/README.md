# Rapport et dossier technique

| Fichier | Contenu |
|---|---|
| `rapport_projet3.pdf` | **Rapport à rendre** (4 pages, limite : 8) — introduction, architecture, fonctionnalités avec captures, difficultés, orientations futures. |
| `dossier_technique.pdf` | Dossier complémentaire (12 pages) — synthèse des deux cours avec schémas, énoncé du projet 3, analyse de conformité point par point, plan d'action. |

Sources LaTeX : `rapport_projet3.tex`, `dossier_technique.tex`, `preambule.tex` (commun).

## Recompiler

```bash
brew install tectonic          # moteur LaTeX autonome, sans privilèges
cd rapport
tectonic -X compile rapport_projet3.tex
tectonic -X compile dossier_technique.tex
```

Les figures proviennent de `../docs/` (captures extraites de la vidéo de démonstration).
