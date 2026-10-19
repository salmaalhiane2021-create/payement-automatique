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

L'application est **internationale et inclusive** : interface et messages
principaux en **français et anglais**, formats régionaux respectés par
utilisateur (pays, ville, téléphone, fuseau horaire, devise, locale), et
**aucune nationalité** collectée — le pays ou l'origine n'est jamais un motif
de blocage (voir [Internationalisation](#internationalisation-langues--formats)).

---

## Sommaire

- [Démarrage en 3 commandes](#démarrage-en-3-commandes)
- [Interface Streamlit (déploiement en ligne)](#interface-streamlit-déploiement-en-ligne)
- [Comptes de démonstration](#comptes-de-démonstration)
- [Scénarios de démonstration](#scénarios-de-démonstration)
- [Comment fonctionne l'IA](#comment-fonctionne-lia)
- [Moteur de décision](#moteur-de-décision)
- [Architecture](#architecture)
- [Base de données & migrations](#base-de-données--migrations)
- [Internationalisation (langues & formats)](#internationalisation-langues--formats)
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

- création de la base SQLite `data/nexpay.db` (**schéma créé et versionné par les
  migrations Alembic**) ;
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

L'interface elle-même est découpée dans le paquet **`streamlit_ui/`** (contexte,
mise en forme localisée, composants, une page par domaine) : `streamlit_app.py`
n'est qu'un point d'entrée. Les interfaces ne font que présenter et collecter,
toute la logique métier vit dans `app/services/` et `app/ml/`.

L'interface est **bilingue (français, anglais)** : sélecteur de langue dans la
barre latérale, et section **« Profil & région »** pour régler pays, ville,
téléphone, format régional (nombres et dates), fuseau horaire et devise
d'affichage. La mise en forme passe par Babel / zoneinfo / phonenumbers : aucun
format national n'est codé en dur dans les pages.

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
verrouillage anti-force brute (5 essais / 15 minutes) que l'API : elle appelle
le service d'authentification partagé `app/services/auth.py` (anti-énumération,
comptes désactivés, préférences régionales).

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

**Politique « step-up »** : certains signaux critiques (appareil inconnu, zone
géographique encore jamais observée *sur le compte*, catégorie à risque,
déplacement très rapide) imposent une vérification renforcée même si le score
reste modéré. C'est la règle suivie par les banques : un petit montant depuis un
appareil inconnu ne peut jamais être accepté sans second facteur. Le pays ou
l'origine de l'utilisateur n'est jamais un facteur en soi : seul l'écart avec
l'historique du compte compte.

Chaque facteur est renvoyé avec un libellé **traduit** au moment de l'analyse
(français ou anglais : « Montant inhabituel » / “Unusual amount”, « Zone
géographique inhabituelle » / “Unusual geographical zone”…), ce qui rend chaque
décision **explicable** pour l'utilisateur (exigence RGPD/IA de transparence).
Les seuils sont configurables dans `.env`.

---

## Architecture

| Couche | Choix | Pourquoi |
| --- | --- | --- |
| API | **FastAPI** + Pydantic | validation automatique, documentation OpenAPI intégrée |
| Persistance | **SQLAlchemy 2** + SQLite | zéro installation ; bascule PostgreSQL par `DATABASE_URL` |
| Migrations | **Alembic** (`migrations/`) | schéma versionné, mise à niveau sans perte de données |
| IA | **scikit-learn** (Isolation Forest) | détection non supervisée, adaptée aux fraudes inconnues |
| Sécurité | bcrypt, PyJWT | hachage lent des secrets, jetons signés HS256 |
| Internationalisation | **Babel**, **zoneinfo**, **phonenumbers** | formatage localisé sans format national codé en dur |
| Interface web | HTML/CSS/JS **sans build** | aucune dépendance npm, graphiques SVG dessinés à la main |
| Interface Cloud | **Streamlit** (`streamlit_app.py` → `streamlit_ui/`) | déploiement en un clic, interface découpée en modules d'affichage |

Aucune bibliothèque front externe (pas de CDN) : l'application FastAPI fonctionne
hors-ligne une fois installée. Les deux interfaces partagent la même logique
métier (`app/services`, `app/ml`) ; seul le transport diffère : HTTP/JWT pour
FastAPI, appels Python directs pour Streamlit.

### Fiabilité des écritures (paiements atomiques)

Chaque confirmation de paiement s'exécute dans **une seule transaction
SQLAlchemy** ; le service `app/services/payment_flow.py` est le seul propriétaire
des `commit` :

- **OTP à usage unique garanti** : la consommation est un *compare-and-set*
  (`UPDATE payment_challenges SET consumed_at = … WHERE id = ? AND consumed_at IS NULL`) ;
  en cas de double soumission simultanée, une seule requête « gagne » ;
- **solde jamais négatif** : le débit est un `UPDATE accounts SET balance = balance - ? WHERE id = ? AND balance >= ?`
  suivi d'une relecture — les paiements concurrents ne peuvent pas se marcher dessus ;
- **tout ou rien** : défi OTP, opération, solde, profil comportemental, alertes et
  journal d'audit sont écrits ensemble ; en cas d'erreur, `rollback` complet
  (aucune écriture partielle). Les chemins « fonds insuffisants » ou « code
  erroné » restent journalisés comme avant ;
- expiration (60 s), 3 tentatives maximum et exigence de code PIN inchangées.

### Textes et langues

Tous les messages — API et interface — passent par `app/i18n.py` (clés stables,
français de référence + anglais). Les interfaces FastAPI et Streamlit ne
contiennent **aucun texte métier en dur** : les facteurs de risque reçoivent
leur libellé traduit au moment de l'analyse, les alertes sont créées dans la
langue de la demande (`lang=fr|en`), et une clé manquante retombe sur le
français puis sur la clé (jamais d'exception).

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
├── streamlit_app.py           # point d'entrée Streamlit (déploiement Cloud)
├── alembic.ini                # configuration Alembic (migrations)
├── requirements.txt
├── .env.example               # toutes les variables configurables
├── .streamlit/
│   └── config.toml            # thème sombre de l'interface Streamlit
├── app/
│   ├── main.py                # application FastAPI (routeurs, erreurs, statiques)
│   ├── config.py              # configuration (.env → Settings + validate_runtime)
│   ├── database.py            # moteur SQLAlchemy, sessions, utilitaires
│   ├── models.py              # tables : users, accounts, transactions, challenges…
│   ├── schemas.py             # validation des entrées/sorties (Pydantic)
│   ├── i18n.py                # catalogues fr/en (interface + messages API)
│   ├── security.py            # bcrypt, JWT, hachage HMAC des codes OTP
│   ├── deps.py                # dépendances FastAPI (utilisateur connecté, langue…)
│   ├── bootstrap.py           # amorçage : migrations + données de démo + modèle
│   ├── migrations.py          # mise à niveau de la base (estampillage des bases anciennes)
│   ├── seed.py                # générateur de données réalistes et de fraudes
│   ├── routers/
│   │   ├── auth.py            # inscription, connexion, PIN, profil
│   │   ├── dashboard.py       # tableau de bord (soldes, catégories, séries)
│   │   ├── transactions.py    # historique filtrable
│   │   ├── payments.py        # couche HTTP : quote / OTP / confirmation / scénarios
│   │   └── security_center.py # profil, anomalies, alertes, appareils, modèle
│   ├── services/              # toute la logique métier partagée (aucune règle dans les interfaces)
│   │   ├── auth.py            # authentification, inscription, préférences (API + Streamlit)
│   │   ├── payment_flow.py    # cycle de paiement atomique, partagé API + Streamlit
│   │   ├── risk_engine.py     # règles pondérées, step-up, décisions, synthèse
│   │   ├── otp.py             # cycle de vie du code à usage unique
│   │   ├── behavior.py        # profil comportemental, abonnements, insights
│   │   ├── ledger.py          # soldes, totaux de période, filtres
│   │   ├── geo.py             # villes, distances, vitesse de déplacement
│   │   ├── alerts.py          # alertes de fraude/sécurité + journal d'audit
│   │   ├── recommendations.py # conseils personnalisés (sécurité & épargne)
│   │   ├── user_prefs.py      # locale, fuseau, devise, téléphone (Babel/zoneinfo/phonenumbers)
│   │   └── fx.py              # abstraction de change (désactivée par défaut)
│   └── ml/
│       ├── features.py        # les 10 variables comportementales (+ versions)
│       ├── anomaly.py         # entraînement, calibration, scoring, évaluation
│       └── artifact_store.py  # stockage des modèles (fichier local ou S3)
├── migrations/                # révisions Alembic (0001 initiale, 0002 profil international…)
├── frontend/                  # SPA sans build : login, tableau de bord,
│   │                          # transactions, paiement, centre de sécurité
│   └── assets/                # api.js, app.js, charts.js (SVG), styles.css
├── streamlit_ui/              # interface Streamlit : contexte, composants, pages
│   ├── context.py             # session, langue, locale, fuseau, devise
│   ├── formatting.py          # montants, pourcentages, dates, libellés localisés
│   ├── components.py          # briques visuelles (hero, badges, indicateurs)
│   ├── shell.py               # barre latérale, navigation, « Profil & région »
│   └── pages/                 # dashboard, transactions, payment, security, model
├── tests/                     # 81 tests + test d'interface (optionnel)
│   ├── test_core.py           # sécurité, OTP, risque, profils (30 tests)
│   ├── test_atomicity.py      # paiements atomiques, usage unique en concurrence
│   ├── test_international.py  # formats, i18n, auth partagée, zone inhabituelle
│   ├── test_migrations.py     # base neuve / base historique sans Alembic
│   ├── test_ui_keys.py        # cohérence des clés i18n de l'interface
│   ├── test_ui_smoke.py       # parcours Streamlit complet (NEXPAY_UI_SMOKE=1)
│   ├── probe_flow.py          # sonde HTTP de bout en bout (37 vérifications)
│   └── README.md              # comment exécuter chaque niveau de test
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
| `PATCH` | `/api/auth/profile` | Mettre à jour ville, pays, téléphone, locale, fuseau, devise |
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
| `GET` | `/api/health` | État du service (base, modèle, seuils, langues) |

Toutes les routes acceptent un paramètre de langue (`?lang=en`) ou l'en-tête
`Accept-Language` ; sans indication, les messages restent en **français**
(les tests d'API s'appuient sur ces textes de référence).

---

## Base de données & migrations

Le schéma est **versionné avec Alembic** (`migrations/versions/`) ; SQLite reste
la base par défaut en local, PostgreSQL est supporté via `DATABASE_URL` (même
script de migration, `render_as_batch` activé pour SQLite).

```powershell
.venv\Scripts\python -m app.migrations           # met la base à niveau (fait aussi au démarrage)
.venv\Scripts\python -m app.migrations --status  # affiche la révision courante
```

| Révision | Contenu |
| --- | --- |
| `0001_initial` | schéma historique de référence (utilisateurs, comptes, opérations, défis OTP, appareils, alertes, audit, profils) |
| `0002_international_profile` | colonnes `country_code`, `locale`, `timezone`, `currency` sur les utilisateurs (défauts FR / fr-FR / Europe/Paris / EUR) |

- Une base **créée avant les migrations** (sans table `alembic_version`) est
  **estampillée automatiquement** à `0001_initial` puis migrée : aucune table
  n'est supprimée et **aucune donnée n'est perdue** ;
- le serveur FastAPI (`run.py`), Streamlit et `app/bootstrap.py` appellent la
  mise à niveau au démarrage : l'application est toujours à la bonne révision ;
- en production, exécutez `python -m app.migrations` dans le pipeline de
  déploiement avant de basculer le trafic.

## Internationalisation (langues & formats)

- **Langues** : français (référence) et **anglais**, pour l'interface Streamlit,
  l'interface FastAPI et les principaux messages de l'API (`?lang=en` ou
  `Accept-Language`). Les clés sont stables (`payment.completed`,
  `factor.montant_inhabituel`…) et centralisées dans `app/i18n.py` ; une clé
  manquante retombe sur le français, puis sur la clé — jamais d'exception.
- **Formats régionaux** : chaque utilisateur dispose de `country_code`
  (ISO 3166-1 alpha-2), `locale` (BCP 47), `timezone` (IANA) et `currency`
  (ISO 4217). Montants, pourcentages et dates sont formatés par **Babel**,
  horodatages convertis par **zoneinfo**, téléphones normalisés en **E.164** par
  **phonenumbers** (`app/services/user_prefs.py`). Aucun format national n'est
  codé en dur dans les interfaces.
- **Inclusion** : aucune nationalité n'est demandée ni stockée. Le pays, la
  ville ou la langue ne sont **jamais** des motifs de blocage ou de suspicion en
  soi. Le seul signal géographique est **lié à l'historique du compte** :
  - `zone_inhabituelle` (step-up) — opération depuis une zone encore jamais
    observée *sur ce compte* (le libellé le dit explicitement) ;
  - vélocité géographique — déplacement **physiquement impossible** (> 900 km/h).
- **Devises** : la devise de base reste l'**EUR**. La conversion est
  **désactivée par défaut** : aucun taux n'est inventé. Pour l'activer,
  renseignez `FX_RATES_JSON` (taux pour 1 EUR) et `FX_RATES_SOURCE` ; sinon les
  interfaces affichent les montants en EUR avec une note explicative. Les
  montants sont simulés : rien n'est débité.
- **Données de démonstration** : françaises (Paris et villes françaises) et
  inchangées ; elles fonctionnent avec n'importe quelle langue d'interface.

---

## Tests

```powershell
.venv\Scripts\python -m pytest tests -q
```

**81 tests unitaires** s'exécutent sur une base SQLite **en mémoire** (le jeu de
démonstration n'est jamais modifié) :

```powershell
.venv\Scripts\python -m pytest tests -q          # 81 tests unitaires
$env:NEXPAY_UI_SMOKE='1'; .venv\Scripts\python -m pytest tests/test_ui_smoke.py -q
```

- `test_core.py` — **sécurité** (bcrypt, politique de mot de passe, JWT,
  empreintes OTP) ; **authentification dynamique** (usage unique, expiration,
  3 tentatives, PIN, purge) ; **moteur de risque** (monotonie, step-up,
  fonds insuffisants, déplacement impossible, démarrage à froid) ; **profil
  comportemental** (abonnements, exclusions) ;
- `test_atomicity.py` — **paiements atomiques** : une seule transaction,
  `rollback` intégral en cas d'erreur, refus si fonds insuffisants, **OTP non
  réutilisable** (y compris entre sessions) et **confirmations concurrentes**
  (threads) qui ne débitent qu'une fois ;
- `test_international.py` — **auth partagée** (anti-énumération, verrouillage,
  préférences), **formats localisés** (montants/%, dates), **i18n** (parité fr/en,
  repli), **zone inhabituelle** fondée sur l'historique et non sur l'origine ;
- `test_migrations.py` — base neuve migrée jusqu'à la tête, **base historique
  estampillée puis migrée sans perte de données**, idempotence, cohérence
  schéma/modèles ;
- `test_ui_keys.py` — cohérence des clés i18n utilisées par l'interface Streamlit ;
- `test_ui_smoke.py` — parcours complet de l'interface Streamlit (connexion,
  5 pages, bascule de langue, scénario de paiement), **désactivé par défaut**
  (`NEXPAY_UI_SMOKE=1`) car il écrit quelques opérations dans la base configurée.

La sonde HTTP de bout en bout (37 vérifications : parcours OTP complet, PIN,
fonds insuffisants, scénario Lagos, concurrence) est documentée dans
`tests/README.md`.

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
| `ENVIRONMENT` | `development` | `production` active les vérifications strictes (clé secrète, CORS) |
| `SECRET_KEY` | *(clé de dev générée)* | signature JWT et HMAC des OTP — **obligatoire en production** |
| `HOST` / `PORT` | `127.0.0.1` / `8000` | écoute du serveur (mettez `0.0.0.0` derrière un reverse-proxy) |
| `CORS_ORIGINS` | *(liste locale en dev)* | origines autorisées, séparées par des virgules ; `*` refusé en production, vide = même origine |
| `DATABASE_URL` | *(SQLite local)* | ex. `postgresql+psycopg://user:pass@localhost/nexpay` |
| `OTP_TTL_SECONDS` | `60` | durée de vie du code à usage unique |
| `OTP_MAX_ATTEMPTS` | `3` | essais avant invalidation du défi |
| `RISK_ACCEPT_MAX` | `35` | seuil d'acceptation automatique |
| `RISK_VERIFY_MAX` | `70` | seuil de blocage |
| `PAYMENT_MIN_AMOUNT` / `PAYMENT_MAX_AMOUNT` | `0.5` / `100000` | bornes des paiements |
| `ACCESS_TOKEN_EXPIRE_MINUTES` | `120` | durée du jeton de session |
| `LOGIN_MAX_ATTEMPTS` / `LOGIN_LOCKOUT_MINUTES` | `5` / `15` | anti-force brute |
| `APP_TIMEZONE` | `Europe/Paris` | fuseau de référence pour l'analyse (chaque utilisateur a le sien) |
| `DEFAULT_LOCALE` / `DEFAULT_CURRENCY` | `fr-FR` / `EUR` | valeurs par défaut des nouveaux comptes |
| `MODEL_STORE_URI` | `file://app/ml/artifacts` | stockage des artefacts du modèle : `file://…` ou `s3://bucket/préfixe` (nécessite `boto3`) |
| `ML_RETRAIN_MIN_INTERVAL_SECONDS` | `300` | délai minimal entre deux réentraînements |
| `ML_TRAINING_LOCK_TTL_SECONDS` | `900` | expiration du verrou d'entraînement (multi-instances) |
| `ML_CONTAMINATION` | `0.04` | proportion d'anomalies attendue |
| `FX_RATES_JSON` | *(vide)* | taux de change « devise: taux pour 1 EUR » ; **désactivé si vide** |
| `FX_RATES_SOURCE` | *(vide)* | libellé documentaire de la source des taux |

Sécurité du déploiement :

- `validate_runtime()` refuse le démarrage en production si `SECRET_KEY` est
  absente (moins de 32 caractères) ou si `CORS_ORIGINS` contient `*` ;
- le serveur écoute par défaut sur `127.0.0.1` ; pour exposer le service,
  définissez `HOST=0.0.0.0` **derrière un reverse-proxy** et listez les
  origines autorisées ;
- les artefacts du modèle peuvent être partagés entre instances
  (`MODEL_STORE_URI`) ; un verrou avec expiration évite deux entraînements
  simultanés, et la version de l'artefact (format + variables) est vérifiée au
  chargement — un artefact incompatible est ignoré et signalé dans l'interface.

---

## Limites du prototype

- **Simulation complète** : aucune passerelle de paiement, aucun débit réel.
- **Pas d'envoi réel de SMS/e-mail** : en développement, le code OTP est
  renvoyé par l'API et affiché dans l'interface pour permettre la démonstration.
- **Langues partielles** : l'interface Streamlit et les messages principaux de
  l'API existent en **français et anglais** ; les analyses détaillées encore
  produites par les services (insights du profil, recommandations, explications
  d'anomalies) et le **journal d'audit** restent en français. Ajouter une langue
  = ajouter un catalogue dans `app/i18n.py` (les clés sont stables et testées).
- **Change désactivé par défaut** : la conversion EUR → devise d'affichage
  n'est active que si `FX_RATES_JSON` est renseigné avec une source fiable ;
  aucun taux n'est inventé et aucun frais de change n'est simulé.
- **SQLite et montants en Float** : suffisants pour un MVP ; en production on
  utiliserait PostgreSQL et un type décimal (`NUMERIC`) pour l'argent.
- **Modèle ré-entraînable à la demande** (`POST /api/security/retrain` ou le
  bouton de l'interface) mais pas de surveillance de dérive en continu ;
  l'entraînement est verrouillé (TTL) pour ne pas s'emballer.
- **Jeu de données synthétique** : les métriques illustrent la démarche,
  elles ne préjugent pas des performances sur des données réelles.
- **Streamlit Community Cloud** : stockage éphémère (base SQLite recréée à
  chaque redémarrage) ; adapté à la démonstration, pas à une persistance de
  production.
- **Mono-processus** : pas de file d'attente ni de limites de débit globales.

---

*Prototype pédagogique — toutes les données sont fictives.*
