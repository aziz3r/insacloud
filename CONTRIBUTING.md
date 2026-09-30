# Travailler sur InsaCloud

## Stratégie de branches

Le projet suit un **GitHub Flow** : une branche stable, des branches courtes.

```
main ──────●──────────●────────────────●──────────►  toujours déployable
            \        /                /
             ●──●──●                 /               feat/api-workers
                                    /
                        ●──●───────●                 fix/faucheur-labels
```

| Branche | Rôle |
|---|---|
| `main` | seule branche durable. La chaîne d'intégration doit y être verte, et l'étage de déploiement s'exécute à chaque envoi. |
| `feat/<sujet>` | une fonctionnalité. Exemples : `feat/api-workers`, `feat/reprise-sur-panne`. |
| `fix/<sujet>` | une correction. Exemple : `fix/faucheur-labels`. |
| `docs/<sujet>` | documentation seule. |

Les branches sont **courtes** : quelques jours au plus. Une branche qui vit
trois semaines diverge de `main` et la fusion devient un travail en soi.

## Le cycle

```bash
git switch -c feat/quotas-par-utilisateur
# … travail …
cd 2_webapp && pytest && ruff check . && bandit -c pyproject.toml -r .
cd ../3_ansible && ansible-lint
git commit
git push -u origin feat/quotas-par-utilisateur
gh pr create
```

La chaîne d'intégration rejoue les mêmes contrôles sur la demande de fusion.
Elle doit être verte avant de fusionner : les dix travaux, sans exception.

## Messages de commit

Une ligne de résumé à l'impératif, sous 72 caractères, puis un corps qui
explique **pourquoi** — le *quoi* se lit dans le diff.

```
Identifier les machines louées par un label Docker

Le Faucheur reconnaissait les machines à leur préfixe de nom. Les
conteneurs de la pile compose s'appelant insacloud_web et insacloud_db,
il les prenait pour des orphelins et détruisait sa propre plateforme.
```

## Avant d'ouvrir une demande de fusion

| Contrôle | Commande |
|---|---|
| Tests | `cd 2_webapp && pytest` |
| Style et erreurs | `ruff check .` |
| Sécurité du code | `bandit -c pyproject.toml -r .` |
| Dépendances | `pip-audit -r requirements.txt` |
| Infrastructure | `cd 3_ansible && ansible-lint` |
| Migrations | `alembic check` |

Et, si le changement touche au déploiement, un passage complet sur le cluster
avec vérification de l'idempotence :

```bash
cd 3_ansible
ansible-playbook -i hosts.ini site.yml --ask-vault-pass    # doit finir failed=0
ansible-playbook -i hosts.ini site.yml --ask-vault-pass    # doit finir changed=0
```

## Secrets

Rien de secret ne va dans le dépôt. Les clés vivent dans
`3_ansible/group_vars/insacloud/vault.yml`, chiffré par Ansible Vault, et le
fichier `.env` de la pile compose est exclu par `.gitignore`.

`gitleaks` analyse l'arbre **et tout l'historique** à chaque envoi : un secret
commis par erreur puis supprimé au commit suivant reste détecté.

## Versions

Les étiquettes suivent le versionnement sémantique (`v1.2.0`). La chaîne
d'intégration étiquette chaque image publiée avec la version, le commit exact
et `latest` : on sait toujours quel code tourne.
