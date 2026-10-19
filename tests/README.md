# Tests

Trois niveaux de vérification, du plus rapide au plus complet.

## 1. Tests unitaires (par défaut)

```powershell
.venv\Scripts\python -m pytest tests -q
```

Base SQLite **en mémoire** : le jeu de démonstration n'est jamais modifié.

| Fichier | Contenu |
| --- | --- |
| `test_core.py` | sécurité (bcrypt, JWT, HMAC OTP), OTP (usage unique, expiration, tentatives, PIN), moteur de risque, profil comportemental |
| `test_atomicity.py` | paiements atomiques, `rollback` intégral, fonds insuffisants, OTP non réutilisable, confirmations concurrentes (threads) |
| `test_international.py` | auth partagée (anti-énumération, verrouillage), formats localisés (Babel), i18n fr/en, facteur `zone_inhabituelle` fondé sur l'historique |
| `test_migrations.py` | base neuve migrée, base historique estampillée puis migrée sans perte de données, idempotence |
| `test_ui_keys.py` | clés i18n utilisées par l'interface Streamlit présentes en fr et en anglais |

## 2. Test de fumée de l'interface Streamlit (optionnel)

```powershell
$env:NEXPAY_UI_SMOKE='1'; .venv\Scripts\python -m pytest tests/test_ui_smoke.py -q
```

Démarre l'application via `streamlit.testing.v1.AppTest` : connexion, navigation
sur les cinq pages, bascule français → anglais, lancement d'un scénario de
paiement. **Écrit** quelques opérations de démonstration (connexion, défi OTP)
dans la base configurée (`DATABASE_URL`, sinon `data/nexpay.db`) — c'est
pourquoi il est désactivé par défaut.

## 3. Sonde HTTP de bout en bout (37 vérifications)

```powershell
# Terminal 1 : serveur de test
.venv\Scripts\python -m uvicorn app.main:app --port 8011 --host 127.0.0.1

# Terminal 2 : sonde
.venv\Scripts\python tests\probe_flow.py
```

La sonde vérifie le parcours réel en HTTP : inscription/connexion, `quote`,
code erroné puis verrouillage du défi, montant inhabituel (PIN exigé, mauvais
PIN, puis validation), scénario « Lagos » (blocage pour déplacement impossible),
usage unique du code, fonds insuffisants, concurrence et messages d'erreur en
français. Elle utilise le compte `yassine@nexpay.fr` et ajoute des opérations
au jeu de démonstration (base `data/nexpay.db` par défaut).

Pour la faire tourner sur une base jetable :

```powershell
$env:DATABASE_URL='sqlite:///C:/chemin/vers/probe.db'
.venv\Scripts\python -m app.seed --force   # amorce le jeu de démonstration
# puis le serveur et la sonde ci-dessus
```
