# Les outils, brique par brique

**[technologies.pdf](technologies.pdf)** — 49 pages.

Ce document ne raconte pas le projet : il prend les **44 technologies** une par
une, rangées en sept couches qui s'empilent. On part d'un dossier vide et, à
chaque brique posée, la plateforme sait faire une chose de plus.

Pour chaque outil, quatre questions, toujours les mêmes :

| | |
|---|---|
| **C'est quoi** | en deux phrases, sans supposer qu'on connaît |
| **Pourquoi celui-là** | les autres possibilités, et ce qui a fait pencher |
| **Son rôle ici** | précisément, avec le fichier concerné |
| **Ce que ça a changé** | ce que la plateforme savait faire avant, et après |

Le chapitre final récapitule les 44 briques avec, pour chacune, ce qui a été
écarté à son profit ; puis quelles briques réalisent chacune des 17
fonctionnalités du cahier des charges.

## Recompiler

```bash
cd technologies && tectonic -X compile technologies.tex
```

## Les illustrations

`figures/` contient des **recadrages** des captures de `soutenance/captures/` :
on ne montre que la zone dont parle le texte, pas la page entière. Pour les
régénérer après une nouvelle capture, le script de découpe est reproduit dans
l'historique git du dossier.

## Les autres documents

| | |
|---|---|
| [guide/](../guide/) | 93 pages — construire le projet pas à pas, depuis les notions |
| [soutenance/](../soutenance/) | 26 pages — le script minuté de la présentation |
| ce document | 49 pages — chaque outil pour lui-même |
