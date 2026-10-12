# Script de soutenance

**[soutenance.pdf](soutenance.pdf)** — 26 pages, à imprimer ou à garder ouvert
pendant la répétition.

Quinze minutes, trois intervenants, démonstration en direct. À chaque geste,
celui qui parle nomme l'outil employé et la fonctionnalité du cahier des
charges qu'il réalise.

Le document contient, pour chaque minute : ce qu'il faut **dire** (encadré
rouge), ce qu'il faut **faire** (encadré bleu), et la capture d'écran de ce que
le public verra à cet instant.

## Recompiler

```bash
cd soutenance && tectonic -X compile soutenance.tex
```

## D'où viennent les illustrations

`captures/` — captures prises sur la plateforme en fonctionnement, pas des
maquettes.

`sorties/` — sorties réelles des commandes, enregistrées au moment de la
préparation. Si le projet évolue, les régénérer :

```bash
docker compose -p insacloud ps --format 'table {{.Name}}\t{{.Status}}' > sorties/pile.txt
docker images --filter 'reference=insacloud_*' --format '{{.Repository}}  {{.Size}}' | sort > sorties/images.txt
curl -s http://127.0.0.1:8088/health | python3 -m json.tool > sorties/health.txt
cd ../2_webapp && .venv/bin/python -m pytest 2>&1 | tail -3 > ../soutenance/sorties/tests.txt
```

La version Markdown du même script, plus facile à modifier, est à la racine :
[SOUTENANCE.md](../SOUTENANCE.md).
