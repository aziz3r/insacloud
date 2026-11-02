# Fiche technique : la sécurité

**[securite.pdf](securite.pdf)** — 48 pages.

Trente-quatre mesures de sécurité, prises une par une. Chacune répond aux mêmes
quatre questions :

- **La menace** — quelle attaque elle traite ;
- **La parade** — par quel mécanisme ;
- **Dans le code** — où, avec les valeurs exactes ;
- **Ce que ça ne couvre pas** — la limite, nommée.

La dernière question n'est pas une concession. Une défense dont on ignore la
limite donne une fausse assurance, et une fausse assurance est pire qu'une
absence de défense : on arrête de surveiller.

## Les dix couches

| # | Couche | Ce qu'elle empêche |
|---|---|---|
| 1 | Le modèle de menace | — (ce qu'on défend, contre qui) |
| 2 | L'identité | qu'on se fasse passer pour un utilisateur |
| 3 | La session et le navigateur | qu'on détourne une session ouverte |
| 4 | Les secrets | qu'un secret traîne en clair quelque part |
| 5 | L'accès aux machines louées | qu'on entre dans la machine d'un autre |
| 6 | Le durcissement des conteneurs | qu'un locataire sorte de sa machine |
| 7 | Le contrôleur et les nœuds | qu'on prenne la main sur un démon Docker |
| 8 | Les données et le réseau | qu'on corrompe l'état ou qu'on écoute le trafic |
| 9 | La chaîne DevSecOps | qu'une faille entre dans le dépôt sans qu'on la voie |
| 10 | Les limites assumées | qu'on se croie protégé là où on ne l'est pas |

Le chapitre 10 contient le tableau des **risques résiduels** — chacun avec sa
gravité et ce qu'il faudrait faire en production — et un récapitulatif
*menace → parade → preuve* de dix-huit lignes, où la colonne « preuve » renvoie
à un test ou à un fichier.

## Compiler

```bash
cd securite && tectonic -X compile securite.tex
```
