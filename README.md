# NexPay AI — Paiement électronique sécurisé & banque intelligente

Prototype MVP d'une application bancaire française qui combine **deux briques** :

1. **Paiement électronique sécurisé** — score de risque 0-100 calculé avant chaque
   validation, décision automatique (accepté / vérification renforcée / bloqué)
   et **authentification dynamique** : code à usage unique (OTP) de 60 secondes,
   à usage unique, validé côté serveur.
2. **Banque intelligente** — tableau de bord, profil comportemental,
   détection d'anomalies par **Isolation Forest**, alertes de fraude en temps
   réel et recommandations personnalisées de sécurité et d'épargne.

> ⚠️ **Simulation uniquement.** Aucune donnée bancaire réelle n'est utilisée,
> aucun paiement réel n'est effectué, aucune carte n'est débitée. Toutes les
> opérations sont simulées sur des données de démonstration générées localement.

---

## Sommaire

- [Démarrage en 3 commandes](#démarrage-en-3-commandes)
- [Interface Streamlit (déploiement en ligne)](#interface-streamlit-déploiement-en-ligne)
- [Comptes de démonstration](#comptes-de-démonstration)
- [Scénarios de démonstration](#scénarios-de-démonstration)
- [Comment fonctionne l'IA](#comment-fonctionne-lia)
- [Moteur de décision](#moteur-de-décision)
- [Architecture](#architecture)
- [Structure du projet](#structure-du-projet)
- [API REST](#api-rest)
- [Tests](#tests)
- [Sécurité](#sécurité)
- [Configuration](#configuration)
- [Limites du prototype](#limites-du-prototype)

---

## Démarrage en 3 commandes

Prérequis : **Python 3.11 ou plus récent** (testé avec Python 3.15), aucun autre
outil à installer (pas de Node.js, pas de base de données externe).

### Windows (PowerShell)

```powershell
py -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
.venv\Scripts\python run.py
```

### macOS / Linux

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python run.py
```

La troisième commande prépare tout automatiquement :

- création de la base SQLite `data/nexpay.db` ;
- **génération des données de démonstration** (8 utilisateurs, ~1 950 opérations
  sur 3 mois, 24 fraudes simulées, alertes, journal d'audit) ;
- **entraînement du modèle Isolation Forest** et calcul des métriques ;
- démarrage du serveur et ouverture du navigateur.

Adresses :

| Service | URL |
| --- | --- |
| Interface web | http://127.0.0.1:8000 (redirige vers la page de connexion) |
| Documentation API (Swagger) | http://127.0.0.1:8000/api/docs |
| État de santé | http://127.0.0.1:8000/api/health |

Commandes utiles :

```powershell
.venv\Scripts\python run.py --reset      # régénère les données de démonstration
.venv\Scripts\python run.py --port 8080  # change le port
.venv\Scripts\python run.py --no-browser # n'ouvre pas le navigateur
.venv\Scripts\python -m app.seed --force # re-génération seule (sans serveur)
.venv\Scripts\python -m pytest tests -q  # tests unitaires
```

---

## Interface Streamlit (déploiement en ligne)

En plus de l'interface FastAPI, le projet contient une **interface Streamlit**
autonome, pensée pour être déployée sur **Streamlit Community Cloud**. Elle
réutilise exactement les mêmes services métier (moteur de risque, codes à usage
unique, Isolation Forest, profil comportemental, alertes, recommandations) ;
la logique du cycle de paiement est partagée dans
`app/services/payment_flow.py`, donc aucune règle n'est dupliquée.

### Lancer en local

```powershell
.venv\Scripts\python -m pip install -r requirements.txt
.venv\Scripts\python -m streamlit run streamlit_app.py
```

L'interface s'ouvre sur **http://localhost:8501**. La base SQLite et le modèle
sont créés automatiquement au premier lancement. Les identifiants de
démonstration sont préremplis sur la page de connexion.

Pour faire tourner les deux interfaces en parallèle, ouvrez deux terminaux :
`run.py` (port 8000) et `streamlit run streamlit_app.py` (port 8501).

### Déployer sur Streamlit Community Cloud

1. Pousser le projet sur GitHub (branche `main`).
2. Aller sur <https://share.streamlit.io> → **Create app** →
   **Deploy a public app from GitHub**.
3. Renseigner :
   - **Repository** : `salmaalhiane2021-create/payement-automatique`
   - **Branch** : `main`
   - **Main file path** : `streamlit_app.py`
4. Cliquer sur **Deploy**. Aucun secret n'est requis : la clé de démonstration
   est générée automatiquement dans `data/` au démarrage.

> **Stockage éphémère.** Le disque de Streamlit Community Cloud est réinitialisé
> à chaque redémarrage : la base SQLite est recréée avec le jeu de démonstration
> complet (8 comptes, ~1 950 opérations, 24 fraudes simulées) et le modèle est
> réentraîné. C'est volontaire pour ce prototype — aucune donnée à conserver.

### Pages disponibles

| Page | Contenu |
| --- | --- |
| **Tableau de bord** | soldes, dépenses du mois, catégories, série 7 jours, abonnements, dernières opérations, alertes |
| **Transactions** | historique filtrable et paginé, statut des opérations bloquées, détail et facteurs de risque |
| **Paiement sécurisé** | 5 scénarios + paiement libre, score, facteurs, code OTP de 60 s, PIN, annulation |
| **Sécurité & IA** | profil comportemental, explications d'anomalies, alertes, appareils, journal d'audit, recommandations |
| **Modèle & données** | fiche du modèle, AUC/précision/rappel, évaluation du moteur complet, réentraînement, statistiques du jeu de données |

L'interface Streamlit est protégée par le même hachage bcrypt et le même
verrouillage anti-force brute (5 essais / 15 minutes) que l'API.

---

## Comptes de démonstration

Les 8 comptes générés partagent le même mot de passe et le même code PIN :

| Compte | E-mail | Mot de passe | PIN |
| --- | --- | --- | --- |
| **Camille Bernard** (principal, Paris) | `demo@nexpay.fr` | `Demo1234!` | `4321` |
| Yassine El Amrani (Lyon) | `yassine@nexpay.fr` | `Demo1234!` | `4321` |
| Léa Moreau (Marseille) | `lea@nexpay.fr` | `Demo1234!` | `4321` |
| Thomas Dubois (Toulouse) | `thomas@nexpay.fr` | `Demo1234!` | `4321` |
| Aïcha Diallo (Lille) | `aicha@nexpay.fr` | `Demo1234!` | `4321` |
| Hugo Lefèvre (Bordeaux) | `hugo@nexpay.fr` | `Demo1234!` | `4321` |
| Inès Rossi (Nice) | `ines@nexpay.fr` | `Demo1234!` | `4321` |
| Karim Benali (Nantes) | `karim@nexpay.fr` | `Demo1234!` | `4321` |

Le bouton « Essayer le compte de démonstration » de l'interface FastAPI
pré-remplit les identifiants ; dans l'interface Streamlit, ils sont préremplis
par défaut.

---

## Scénarios de démonstration

La page **Paiement sécurisé** propose cinq scénarios prêts à l'emploi qui
illustrent chaque décision du moteur :

| Scénario | Opération | Décision attendue |
| --- | --- | --- |
| **Achat habituel** | Boulangerie, 7,40 € à 12 h, Paris | `accept` — code OTP simple |
| **Montant inhabituel** | Boutique en ligne, 850 € à 15 h | `verify` — OTP **+ code PIN** |
| **Appareil inconnu** | 679 € depuis un navigateur jamais vu | `verify` — vérification imposée (step-up) |
| **Paiement de nuit** | Jeux en ligne, 380 € à 3 h du matin | `verify` — OTP + PIN |
| **Voyage impossible** | 6,20 € à Paris à 21 h 00 puis 620 € à **Lagos** à 21 h 04 | `block` — « Déplacement de 4 708 km (70 622 km/h) : physiquement impossible » |

Le scénario « voyage impossible » enchaîne automatiquement deux paiements :
le moteur compare la position et l'horodatage simulés des deux opérations et
bloque la seconde (règle de **vélocité géographique**, indépendante du montant :
elle bloque aussi bien 620 € que 2 €).

Chaque paiement répond à la même mécanique :

1. **Analyse** — le moteur de risque renvoie un score 0-100, une décision et
   la liste des facteurs explicatifs en français ;
2. **Défi** — un code à 6 chiffres valable **60 secondes** est généré
   (en simulation, il s'affiche dans l'interface : aucun SMS n'est envoyé) ;
3. **Validation** — le code est vérifié **côté serveur**, marqué consommé
   (usage unique), et le code PIN est exigé en cas de vérification renforcée ;
4. **Comptabilisation** — le débit est enregistré à l'heure simulée, le profil
   comportemental est rafraîchi et d'éventuelles alertes sont créées.

Règles de l'OTP : 60 s de validité, **un seul usage**, 3 tentatives maximum
puis invalidation du défi, jamais stocké en clair (empreinte HMAC-SHA256 liée
à l'identifiant du défi).

---

## Comment fonctionne l'IA

### Détection d'anomalies (Isolation Forest)

Le modèle décrit le **comportement de dépense** de l'utilisateur à partir de
10 variables comparant chaque opération à ses habitudes :

| Variable | Signification |
| --- | --- |
| `amount_log` | log10 du montant (compresse les très gros montants) |
| `amount_zscore` | écart au montant médian (écarts absolus médians, robuste) |
| `amount_vs_p90` | montant / 90ᵉ centile historique |
| `hour_unusual` | heure inhabituelle (lissée avec les heures voisines, plafonnée de jour) |
| `new_device` | appareil jamais utilisé |
| `new_location` | ville jamais vue |
| `txn_count_1h` | rythme d'opérations dans l'heure |
| `geo_velocity` | vitesse de déplacement implicite (km/h) |
| `night_time` | opération entre 23 h et 6 h |
| `new_channel` | canal de paiement inhabituel |

Points importants :

- entraînement **par utilisateur** puis fusion (le modèle apprend des
  habitudes individuelles) ;
- les opérations **bloquées, frauduleuses et les encaissements** (salaire,
  virements reçus) sont exclus de l'entraînement : le modèle décrit les
  dépenses ponctuelles, pas les revenus ni les prélèvements attendus ;
- score d'anomalie **calibré par quantiles empiriques** puis ramené entre 0 et
  1 pour l'affichage (« IA 0.92 ») ;
- repli statistique si le modèle n'est pas disponible (démarrage à froid).

### Évaluation sur la vérité terrain simulée

Le jeu de démonstration contient 24 fraudes étiquetées. À la génération, le
prototype affiche :

- **Modèle** : précision ≈ 0,22 · rappel ≈ 0,73 · F1 ≈ 0,34 · **AUC ≈ 0,93** ;
- **Système complet** (règles + IA) : **100 % des fraudes** bloquées ou
  escaladées, **≈ 2 % de friction** sur les paiements légitimes.

(les valeurs exactes varient légèrement d'une génération à l'autre, les
horodatages étant recalés sur la date d'exécution)

La comparaison entre précision et rappel montre le compromis classique en
détection de fraude : à 4 % de contamination, le modèle signale plus large
qu'il n'a raison, mais le **moteur de décision** (règles + IA + step-up)
rattrape les fraudes tout en limitant la friction. Ces chiffres portent sur
des données synthétiques : ils illustrent la démarche, pas une performance de
production.

---

## Moteur de décision

Le score final (0-100) additionne des **règles métier pondérées** (~80 points)
et le **score d'anomalie IA** (~20 points) :

| Décision | Seuil | Conséquence |
| --- | --- | --- |
| `accept` | < 35 | Code OTP, débit immédiat |
| `verify` | 35 à 69 | OTP **+ code PIN**, puis débit |
| `block` | ≥ 70 | Paiement refusé, alerte créée |

Trois règles forcent un blocage quel que soit le score :

- **vélocité géographique impossible** (≥ 900 km/h entre deux paiements) ;
- **fonds insuffisants** ;
- score au-delà du seuil de blocage.

**Politique « step-up »** : certains signaux critiques (appareil inconnu,
pays jamais vu, catégorie à risque, déplacement très rapide) imposent une
vérification renforcée même si le score reste modéré. C'est la règle suivie
par les banques : un petit montant depuis un appareil inconnu ne peut jamais
être accepté sans second facteur.

Chaque facteur est renvoyé avec un libellé français (« Montant inhabituel »,
« Déplacement impossible », « Anomalie comportementale (IA) »…), ce qui rend
chaque décision **explicable** pour l'utilisateur (exigence RGPD/IA de
transparence). Les seuils sont configurables dans `.env`.

---

## Architecture

| Couche | Choix | Pourquoi |
| --- | --- | --- |
| API | **FastAPI** + Pydantic | validation automatique, documentation OpenAPI intégrée |
| Persistance | **SQLAlchemy 2** + SQLite | zéro installation ; bascule PostgreSQL par `DATABASE_URL` |
| IA | **scikit-learn** (Isolation Forest) | détection non supervisée, adaptée aux fraudes inconnues |
| Sécurité | bcrypt, PyJWT | hachage lent des secrets, jetons signés HS256 |
| Interface web | HTML/CSS/JS **sans build** | aucune dépendance npm, graphiques SVG dessinés à la main |
| Interface Cloud | **Streamlit** (`streamlit_app.py`) | déploiement en un clic sur Streamlit Community Cloud |

Aucune bibliothèque front externe (pas de CDN) : l'application FastAPI fonctionne
hors-ligne une fois installée. Les deux interfaces partagent la même logique
métier (`app/services`, `app/ml`) ; seul le transport diffère : HTTP/JWT pour
FastAPI, appels Python directs pour Streamlit.

### Flux d'un paiement

```
Navigation ──> POST /api/payments/quote ──── analyse risque + IA
                              │
                 accept ──> OTP (60 s)
                 verify ──> OTP + PIN        ──> POST /api/payments/confirm
                 block  ──> refus + alerte        (validation serveur, usage unique)
                                                            │
                                             débit + profil + alertes + audit
```

---

## Structure du projet

```
payement automatique/
├── run.py                     # lancement en une commande (FastAPI)
├── streamlit_app.py           # interface Streamlit (déploiement Cloud)
├── requirements.txt
├── .env.example               # toutes les variables configurables
├── .streamlit/
│   └── config.toml            # thème sombre de l'interface Streamlit
├── app/
│   ├── main.py                # application FastAPI (routeurs, erreurs, statiques)
│   ├── config.py              # configuration (.env → Settings)
│   ├── database.py            # moteur SQLAlchemy, sessions, utilitaires
│   ├── models.py              # tables : users, accounts, transactions, challenges…
│   ├── schemas.py             # validation des entrées/sorties (Pydantic)
│   ├── security.py            # bcrypt, JWT, hachage HMAC des codes OTP
│   ├── deps.py                # dépendances FastAPI (utilisateur connecté, IP…)
│   ├── bootstrap.py           # amorçage : base + données de démo + modèle
│   ├── seed.py                # générateur de données réalistes et de fraudes
│   ├── routers/
│   │   ├── auth.py            # inscription, connexion, PIN, profil
│   │   ├── dashboard.py       # tableau de bord (soldes, catégories, séries)
│   │   ├── transactions.py    # historique filtrable
│   │   ├── payments.py        # couche HTTP : quote / OTP / confirmation / scénarios
│   │   └── security_center.py # profil, anomalies, alertes, appareils, modèle
│   ├── services/
│   │   ├── payment_flow.py    # cycle de paiement partagé API + Streamlit
│   │   ├── risk_engine.py     # règles pondérées, step-up, décisions, synthèse
│   │   ├── otp.py             # cycle de vie du code à usage unique
│   │   ├── behavior.py        # profil comportemental, abonnements, insights
│   │   ├── ledger.py          # soldes, totaux de période, filtres
│   │   ├── geo.py             # villes, distances, vitesse de déplacement
│   │   ├── alerts.py          # alertes de fraude/sécurité + journal d'audit
│   │   └── recommendations.py # conseils personnalisés (sécurité & épargne)
│   └── ml/
│       ├── features.py        # les 10 variables comportementales
│       └── anomaly.py         # entraînement, calibration, scoring, évaluation
├── frontend/                  # SPA sans build : login, tableau de bord,
│   │                          # transactions, paiement, centre de sécurité
│   └── assets/                # api.js, app.js, charts.js (SVG), styles.css
├── tests/
│   └── test_core.py           # 30 tests : sécurité, OTP, risque, profils
└── data/                      # base SQLite + clé de développement (générés)
```

---

## API REST

Documentation interactive : **http://127.0.0.1:8000/api/docs**

| Méthode | Route | Rôle |
| --- | --- | --- |
| `POST` | `/api/auth/register` | Créer un compte |
| `POST` | `/api/auth/login` | Connexion (JWT, verrouillage anti-force brute) |
| `GET` | `/api/auth/me` | Profil de l'utilisateur connecté |
| `POST` | `/api/auth/pin` | Définir / modifier le code PIN |
| `GET` | `/api/dashboard` | Soldes, dépenses du mois par catégorie, séries, alertes |
| `GET` | `/api/transactions` | Historique filtrable (période, catégorie, recherche…) |
| `GET` | `/api/transactions/categories` | Catégories disponibles |
| `GET` | `/api/payments/context` | Solde, appareils de confiance, villes |
| `GET` | `/api/payments/scenarios` | Scénarios de démonstration |
| `POST` | `/api/payments/quote` | **Analyse de risque + création du défi OTP** |
| `GET` | `/api/payments/challenge/{id}` | État du défi (secondes restantes, essais) |
| `POST` | `/api/payments/challenge/{id}/cancel` | Annuler le défi |
| `POST` | `/api/payments/confirm` | **Valider l'OTP (+ PIN) et exécuter le débit** |
| `GET` | `/api/security/profile` | Profil comportemental complet + recommandations |
| `GET` | `/api/security/anomalies` | Opérations atypiques détectées par l'IA |
| `GET` | `/api/security/alerts` | Fil des alertes (fraude, anomalie, sécurité) |
| `POST` | `/api/security/alerts/{id}/read` | Marquer une alerte comme lue |
| `GET` | `/api/security/audit` | Journal d'audit du compte |
| `GET` | `/api/security/recommendations` | Conseils personnalisés sécurité & épargne |
| `GET` | `/api/security/model-card` | Fiche du modèle d'IA (métriques, variables) |
| `GET` | `/api/security/devices` | Appareils connus et gestion de confiance |
| `GET` | `/api/health` | État du service (base, modèle, seuils) |

---

## Tests

```powershell
.venv\Scripts\python -m pytest tests -q
```

**30 tests** s'exécutent sur une base SQLite **en mémoire** (le jeu de
démonstration n'est jamais modifié) :

- **sécurité** : hachage bcrypt mots de passe/PIN, politique de mot de passe,
  jetons JWT (aller-retour et rejet d'une altération), empreintes OTP liées au défi ;
- **authentification dynamique** : code à 6 chiffres jamais stocké en clair,
  **usage unique**, **expiration**, verrouillage après 3 tentatives, exigence
  du PIN en vérification renforcée, purge des défis expirés ;
- **moteur de risque** : monotonie du score (montant, heure), step-up appareil
  inconnu, blocage fonds insuffisants, **blocage déplacement impossible**,
  démarrage à froid sans erreur ;
- **profil comportemental** : détection des abonnements, exclusion du loyer,
  exclusion des fraudes et opérations bloquées.

---

## Sécurité

| Mesure | Détail |
| --- | --- |
| Mots de passe | **bcrypt** (coût 12), sel aléatoire, jamais en clair |
| Code PIN | haché de la même façon, espace de noms distinct |
| OTP | 6 chiffres générés par `secrets`, **HMAC-SHA256** lié à l'identifiant du défi, jamais persisté en clair |
| Durée de vie OTP | **60 s**, usage unique, 3 tentatives puis invalidation |
| Sessions | **JWT HS256** signés, expiration 120 min |
| Anti-force brute | verrouillage du compte après 5 échecs (15 min), compteur sur les défis OTP |
| Validation | Pydantic sur toutes les entrées + messages d'erreur en français |
| Secrets | variables d'environnement / `.env` (jamais commité) ; clé de dev générée dans `data/dev_secret.key` (git-ignoré) |
| Traçabilité | journal d'audit (connexions, paiements, décisions de sécurité) |
| Montants | bornés entre 0,50 € et 100 000 € par configuration |

---

## Configuration

Toutes les valeurs sont surchargeables par `.env` (voir `.env.example`) :

| Variable | Défaut | Rôle |
| --- | --- | --- |
| `SECRET_KEY` | *(clé de dev générée)* | signature JWT et HMAC des OTP |
| `OTP_TTL_SECONDS` | `60` | durée de vie du code à usage unique |
| `OTP_MAX_ATTEMPTS` | `3` | essais avant invalidation du défi |
| `RISK_ACCEPT_MAX` | `35` | seuil d'acceptation automatique |
| `RISK_VERIFY_MAX` | `70` | seuil de blocage |
| `ML_CONTAMINATION` | `0.04` | proportion d'anomalies attendue |
| `ACCESS_TOKEN_EXPIRE_MINUTES` | `120` | durée du jeton de session |
| `LOGIN_MAX_ATTEMPTS` / `LOGIN_LOCKOUT_MINUTES` | `5` / `15` | anti-force brute |
| `DATABASE_URL` | *(SQLite local)* | ex. `postgresql+psycopg://user:pass@localhost/nexpay` |
| `APP_TIMEZONE` | `Europe/Paris` | référence de l'analyse comportementale |

---

## Limites du prototype

- **Simulation complète** : aucune passerelle de paiement, aucun débit réel.
- **Pas d'envoi réel de SMS/e-mail** : en développement, le code OTP est
  renvoyé par l'API et affiché dans l'interface pour permettre la démonstration.
- **SQLite et montants en Float** : suffisants pour un MVP ; en production on
  utiliserait PostgreSQL et un type décimal (`NUMERIC`) pour l'argent.
- **Modèle ré-entraînable à la demande** (`POST /api/security/retrain`) mais
  pas de surveillance de dérive en continu.
- **Jeu de données synthétique** : les métriques illustrent la démarche,
  elles ne préjugent pas des performances sur des données réelles.
- **Streamlit Community Cloud** : stockage éphémère (base SQLite recréée à
  chaque redémarrage) ; adapté à la démonstration, pas à une persistance de
  production.
- **Mono-processus** : pas de file d'attente ni de limites de débit globales.

---

*Prototype pédagogique — toutes les données sont fictives.*
