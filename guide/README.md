# Guide technique InsaCloud

Un livre de 93 pages qui explique le projet de bout en bout, pour quelqu'un qui
n'a jamais construit d'infrastructure : la théorie (conteneurs, WSGI,
idempotence, DevSecOps), l'architecture, chaque composant avec son code, le
déploiement et la vérification.

**[guide_insacloud.pdf](guide_insacloud.pdf)**

## Recompiler

```bash
cd guide
tectonic -X compile guide_insacloud.tex
```

Les figures `figures/*.pdf` sont converties depuis les SVG de `docs/`, eux-mêmes
engendrés par `docs/schemas/`. Pour les régénérer après un changement de
schéma :

```bash
python3 docs/schemas/tout.py
python3 -c "
import cairosvg
for n in ('architecture', 'cycle-de-vie', 'base-de-donnees', 'chaine-ci-cd'):
    cairosvg.svg2pdf(url=f'docs/{n}.svg', write_to=f'guide/figures/{n}.pdf')
"
```

## Organisation

| Fichier | Contenu |
|---|---|
| `guide_insacloud.tex` | document principal et couverture |
| `preambule.tex` | polices, couleurs, encadrés, style des extraits de code |
| `00-avant-propos.tex` | à qui s'adresse le guide, le résultat en images |
| `01-theorie.tex` | les notions : cloud, conteneurs, web, IaC, DevSecOps |
| `02-architecture.tex` | la carte du territoire, le modèle de données, le cycle de vie |
| `03-construction.tex` | chaque composant, son code et ses décisions |
| `04-deploiement.tex` | Vagrant, Ansible, nginx, systemd, docker compose |
| `05-verification.tex` | tests, chaîne d'intégration, défauts trouvés |
| `06-annexes.tex` | refaire le projet, commandes, erreurs fréquentes, glossaire |
