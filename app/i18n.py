"""
Internationalisation (i18n) : catalogues de messages français / anglais.

Principes
---------
* Le **français reste la langue par défaut** (comportement existant, tests et
  API inchangés par défaut).
* Chaque message est identifié par une clé stable (`payment.completed`,
  `factor.appareil_inconnu`…) : le texte n'est jamais codé en dur dans les
  interfaces ni dans les services partagés.
* Ajouter une langue = ajouter un dictionnaire dans `CATALOGS` ; les clés
  manquantes retombent sur le français, puis sur la clé elle-même (jamais
  d'erreur d'exécution).

Utilisation
-----------
    from app.i18n import t
    t("payment.completed", "en", amount="12.50 €", merchant="Café")

Le paramètre `lang` accepte un code court (« fr », « en ») ou une locale
(« fr-FR », « en_US ») : seule la langue est retenue.
"""

from __future__ import annotations

DEFAULT_LANGUAGE = "fr"

# Langues réellement traduites (code -> nom affiché dans le sélecteur)
LANGUAGE_LABELS: dict[str, str] = {
    "fr": "Français",
    "en": "English",
}


def normalize_language(value: str | None, default: str = DEFAULT_LANGUAGE) -> str:
    """Ne conserve que la langue d'un code de locale (« en-US » -> « en »)."""
    if not value:
        return default
    code = str(value).strip().lower().replace("_", "-").split("-")[0]
    return code if code else default


def language_from_locale(locale: str | None) -> str:
    """Langue de l'interface déduite de la locale enregistrée par l'utilisateur."""
    return normalize_language(locale)


def t(key: str, lang: str | None = None, **params: object) -> str:
    """
    Traduit `key` dans la langue demandée, avec interpolation de paramètres.

    Repli silencieux : langue demandée -> français -> clé brute. Les valeurs
    inconnues ne provoquent jamais d'exception (protection des interfaces).
    """
    wanted = normalize_language(lang)
    catalogs = (CATALOGS.get(wanted, {}), CATALOGS[DEFAULT_LANGUAGE])
    text: str | None = None
    for catalog in catalogs:
        if key in catalog:
            text = catalog[key]
            break
    if text is None:
        return key
    if not params:
        return text
    try:
        return text.format(**params)
    except (KeyError, IndexError):
        return text


def catalog_keys(lang: str) -> set[str]:
    """Ensemble des clés traduites (utilisé par les tests de complétude)."""
    return set(CATALOGS.get(normalize_language(lang), {}).keys())


# ---------------------------------------------------------------------------
# Catalogue français (langue de référence)
# ---------------------------------------------------------------------------
_CATALOG_FR: dict[str, str] = {
    # --- Messages applicatifs partagés (API + interfaces) ---------------
    "common.date_at": "{date} à {time}",
    "common.time": "{hours} h {minutes:02d}",
    "common.none": "—",
    "common.yes": "Oui",
    "common.no": "Non",
    "common.all": "Toutes",
    "common.current_hour": "Heure actuelle",
    "common.read": "Lue",
    "common.unread": "Non lue",
    "common.new": "Nouveau",
    "common.loading": "Chargement…",
    "common.offline_note": "Données de simulation, aucun paiement réel.",

    # --- Authentification ------------------------------------------------
    "auth.invalid_credentials": "Adresse e-mail ou mot de passe incorrect.",
    "auth.locked": (
        "Compte temporairement verrouillé après trop de tentatives. "
        "Réessayez dans {minutes} minute(s)."
    ),
    "auth.inactive": "Ce compte est désactivé.",
    "auth.register.conflict": "Un compte existe déjà avec cette adresse.",
    "auth.register.pin_invalid": "Le code PIN doit contenir 4 à 6 chiffres.",
    "auth.register.phone_invalid": "Numéro de téléphone invalide : utilisez le format international (+33 6 12 34 56 78).",
    "auth.register.currency_invalid": "Devise inconnue : utilisez un code ISO 4217 valide (EUR, USD, GBP…).",
    "auth.register.timezone_invalid": "Fuseau horaire inconnu : utilisez un identifiant IANA (Europe/Paris, America/New_York…).",
    "auth.register.locale_invalid": "Langue ou locale invalide (exemple : fr-FR, en-US).",
    "auth.profile.updated": "Vos préférences régionales ont été mises à jour.",
    "auth.token_expired": "Session expirée ou jeton invalide.",
    "auth.required": "Authentification requise.",

    # --- Décisions et statuts -------------------------------------------
    "decision.accept": "Paiement accepté",
    "decision.verify": "Vérification renforcée requise",
    "decision.block": "Paiement bloqué",
    "decision.accept_short": "Accepté — code à usage unique requis",
    "decision.verify_short": "Vérification renforcée — code + PIN",
    "decision.block_short": "Paiement bloqué",
    "status.completed": "Exécutée",
    "status.blocked": "Bloquée",
    "status.pending": "En attente",
    "status.cancelled": "Annulée",
    "severity.critique": "Critique",
    "severity.eleve": "Élevé",
    "severity.moyen": "Moyen",
    "severity.faible": "Faible",
    "kind.fraude": "Fraude",
    "kind.anomalie": "Anomalie",
    "kind.securite": "Sécurité",
    "kind.info": "Information",

    # --- Paiement --------------------------------------------------------
    "payment.insufficient_funds": (
        "Paiement refusé : solde insuffisant ({available} disponibles "
        "pour {amount} demandés)."
    ),
    "payment.account_missing": "Aucun compte courant associé.",
    "payment.completed": "Paiement de {amount} vers « {merchant} » validé.",
    "payment.error_code_required": "Saisissez les 6 chiffres du code à usage unique.",
    "risk.summary.accept": (
        "Score de risque {score}/100 : opération conforme à vos habitudes. "
        "Un code à usage unique reste nécessaire pour valider."
    ),
    "risk.summary.verify": (
        "Score de risque {score}/100. Signaux détectés : {reasons}. "
        "Validez avec le code à usage unique ET votre code PIN."
    ),
    "risk.summary.block": (
        "Paiement de {amount} bloqué : score de risque {score}/100 "
        "({reasons})."
    ),
    "risk.summary.block_hard": "Paiement refusé par sécurité. {reason}",
    "risk.reasons_default": "comportement inhabituel",
    "risk.otp_required": "Code à usage unique requis",

    # --- Facteurs de risque (libellés) ----------------------------------
    "factor.montant_inhabituel": "Montant inhabituel",
    "factor.montant_eleve": "Montant élevé",
    "factor.solde_insuffisant": "Fonds insuffisants",
    "factor.compte_presque_vide": "Solde mis en danger",
    "factor.heure_inhabituelle": "Heure inhabituelle",
    "factor.appareil_inconnu": "Appareil inconnu",
    "factor.localisation_inhabituelle": "Lieu inhabituel",
    "factor.zone_inhabituelle": "Zone géographique inhabituelle",
    "factor.velocite_impossible": "Déplacement impossible",
    "factor.velocite_suspecte": "Déplacement très rapide",
    "factor.frequence_anormale": "Rythme d'opérations anormal",
    "factor.nouveau_beneficiaire": "Nouveau bénéficiaire",
    "factor.categorie_sensible": "Catégorie à risque",
    "factor.canal_inhabituel": "Canal inhabituel",
    "factor.anomalie_ia": "Anomalie comportementale (IA)",
    "factor.step_up": "Vérification renforcée imposée",

    # --- OTP -------------------------------------------------------------
    "otp.expired": "Code expiré ({ttl} s). Relancez le paiement pour un nouveau code.",
    "otp.consumed": "Ce code a déjà été utilisé. Relancez l'opération pour en obtenir un nouveau.",
    "otp.too_many": "Défi invalidé après trop de tentatives. Relancez le paiement pour un nouveau code.",
    "otp.invalidated": "Trop de tentatives : ce code est invalidé par sécurité.",
    "otp.incorrect": "Code incorrect. {remaining} tentative(s) restante(s).",
    "otp.incorrect_final": "Code incorrect. Défi invalidé après {max} tentatives.",
    "otp.pin_required": "Code PIN requis pour valider cette opération sensible.",
    "otp.pin_incorrect": "Code PIN incorrect. {remaining} tentative(s) restante(s).",
    "otp.pin_final": "Code PIN incorrect. Défi invalidé après {max} tentatives.",
    "otp.validated": "Code validé.",

    # --- Alertes (stockées dans la langue de l'utilisateur) --------------
    "alert.failed_validation.title": "Tentative de validation échouée",
    "alert.failed_validation.message": "Un code de paiement incorrect a été saisi pour l'opération vers « {merchant} » ({message})",
    "alert.new_device.title": "Nouvel appareil utilisé",
    "alert.new_device.message": (
        "Un paiement a été effectué depuis « {device} », un appareil non vérifié. "
        "Si ce n'est pas le vôtre, changez votre mot de passe et votre code PIN."
    ),
    "alert.blocked.title": "Paiement bloqué : {merchant} ({amount})",
    "alert.blocked.message": "Une opération de {amount} vers « {merchant} » a été refusée. Motifs : {reasons}.",
    "alert.blocked.fallback_reason": "comportement inhabituel",
    "alert.anomaly.title": "Opération atypique : {merchant} ({amount})",
    "alert.anomaly.message": (
        "Cette opération a été exécutée mais le modèle d'IA l'a jugée inhabituelle pour votre compte. "
        "{reasons}Vérifiez-la ; si vous ne la reconnaissez pas, signalez-la depuis le centre de sécurité."
    ),

    # --- Interface : navigation -----------------------------------------
    "app.title": "NexPay AI — Banque & paiement sécurisé",
    "app.brand_subtitle": "BANQUE INTELLIGENTE",
    "app.prototype": "Prototype — paiement simulé",
    "nav.navigation": "Navigation",
    "nav.dashboard": "📊 Tableau de bord",
    "nav.transactions": "🧾 Transactions",
    "nav.payment": "🔐 Paiement sécurisé",
    "nav.security": "🛡️ Sécurité & IA",
    "nav.model": "🧠 Modèle & données",
    "nav.logout": "Se déconnecter",
    "nav.otp_ttl": "Code à usage unique : {ttl} s",
    "nav.thresholds": "Seuils : accept ≤ {accept} · verify ≤ {verify}",
    "nav.unread_alerts": "🔔 {count} alerte(s) non lue(s)",
    "nav.no_unread": "✅ Aucune alerte non lue",
    "nav.language": "Langue",
    "nav.region": "Profil & région",

    # --- Interface : connexion -------------------------------------------
    "login.headline": "Le paiement, sécurisé par l'IA.",
    "login.intro": (
        "Chaque opération est analysée avant validation : score de risque de 0 à 100, "
        "code à usage unique de 60 secondes, détection d'anomalies par Isolation Forest "
        "et conseils personnalisés."
    ),
    "login.feature_otp_title": "🔐 Authentification dynamique",
    "login.feature_otp_text": "Code à 6 chiffres, à usage unique, valable 60 s et vérifié côté serveur.",
    "login.feature_ai_title": "🧠 Intelligence artificielle",
    "login.feature_ai_text": "Isolation Forest apprend vos habitudes et signale les opérations atypiques.",
    "login.feature_decision_title": "🛡️ Décision automatique",
    "login.feature_decision_text": "Accepter, demander une vérification renforcée ou bloquer — avec des motifs lisibles.",
    "login.simulation_notice": "Prototype pédagogique — aucun paiement réel, aucune donnée de carte demandée.",
    "login.title": "Connexion",
    "login.prefilled": "Identifiants de démonstration préremplis — cliquez simplement sur « Se connecter ».",
    "login.email": "Adresse e-mail",
    "login.password": "Mot de passe",
    "login.submit": "Se connecter",
    "login.demo_accounts": "Comptes de démonstration",
    "login.demo_caption": (
        "Les huit comptes du jeu de données partagent le même mot de passe. "
        "Le compte `demo@nexpay.fr` est le plus riche (historique, fraudes simulées)."
    ),

    # --- Interface : profil et région ------------------------------------
    "region.title": "Profil & région",
    "region.caption": "Ces préférences n'affectent que l'affichage (langue, fuseau, devise) et vos coordonnées.",
    "region.country": "Pays ou région",
    "region.city": "Ville",
    "region.phone": "Téléphone (format international)",
    "region.timezone": "Fuseau horaire",
    "region.currency": "Devise d'affichage",
    "region.locale": "Format régional (langue des nombres et dates)",
    "region.save": "Enregistrer",
    "region.saved": "Préférences enregistrées (langue : {lang}, fuseau : {tz}, devise : {currency}).",
    "region.currency_unavailable": (
        "Les montants restent affichés en EUR : aucune source de change fiable n'est configurée "
        "(voir FX_RATES_JSON). Aucune conversion n'est inventée."
    ),
    "region.currency_converted": "Montants convertis depuis EUR avec la source configurée : {source}.",
    "region.simulation_amounts": "Montants simulés — aucune devise réelle n'est débitée.",

    # --- Interface : tableau de bord -------------------------------------
    "dashboard.kicker": "Tableau de bord",
    "dashboard.greeting": "Bonjour, {name} 👋",
    "dashboard.subtitle": "Vue d'ensemble au {date}.",
    "dashboard.unread": "{count} alerte(s) de sécurité à consulter.",
    "dashboard.kpi.balance": "Solde courant",
    "dashboard.kpi.balance_delta": "{amount} ce mois",
    "dashboard.kpi.savings": "Épargne",
    "dashboard.kpi.spending": "Dépenses (mois)",
    "dashboard.kpi.spending_share": "{pct} des revenus",
    "dashboard.kpi.income": "Revenus (mois)",
    "dashboard.balance_evolution": "Évolution du solde — 30 derniers jours",
    "dashboard.balance_series": "Solde",
    "dashboard.spending_categories": "Dépenses par catégorie — 30 jours",
    "dashboard.no_spending": "Pas encore de dépenses sur la période.",
    "dashboard.daily_spending": "Dépenses des 7 derniers jours",
    "dashboard.daily_series": "Dépenses",
    "dashboard.subscriptions": "Abonnements détectés",
    "dashboard.subscriptions_total": "Total mensuel",
    "dashboard.no_subscriptions": "Aucun abonnement récurrent identifié.",
    "dashboard.recent_operations": "Dernières opérations",
    "dashboard.recent_alerts": "Alertes récentes",
    "dashboard.no_alerts": "Aucune alerte pour le moment.",
    "dashboard.no_transactions": "Aucune opération.",
    "dashboard.no_account": "Aucun compte courant associé.",

    # --- Interface : transactions ---------------------------------------
    "transactions.kicker": "Historique",
    "transactions.title": "Transactions",
    "transactions.subtitle": (
        "Toutes vos opérations, y compris les tentatives bloquées par le moteur de sécurité. "
        "Une opération bloquée n'a pas eu lieu : elle n'affecte pas votre solde."
    ),
    "transactions.period": "Période",
    "transactions.period_7": "7 jours",
    "transactions.period_30": "30 jours",
    "transactions.period_90": "90 jours",
    "transactions.period_year": "1 an",
    "transactions.period_all": "Tout",
    "transactions.category": "Catégorie",
    "transactions.search": "Rechercher",
    "transactions.search_placeholder": "Marchand, libellé ou ville…",
    "transactions.only_flagged": "Signalées",
    "transactions.page_info": "{total} opération(s) — page {page} / {pages}",
    "transactions.none_match": "Aucune opération ne correspond à ces filtres.",
    "transactions.previous": "← Précédent",
    "transactions.next": "Suivant →",
    "transactions.inspect": "Inspecter une opération",
    "transactions.choose": "Choisir une opération de cette page…",
    "transactions.operation": "Opération",
    "transactions.merchant": "Marchand",
    "transactions.date": "Date",
    "transactions.city": "Ville",
    "transactions.amount": "Montant",
    "transactions.risk": "Risque",
    "transactions.ai_score": "Score IA",
    "transactions.status": "Statut",
    "transactions.decision": "Décision",
    "transactions.channel": "Canal",
    "transactions.location": "Lieu",
    "transactions.factors": "Facteurs de risque",
    "transactions.factor": "Facteur",
    "transactions.detail": "Détail",
    "transactions.points": "Points",
    "transactions.severity": "Gravité",
    "transactions.operations": "Opérations",
    "transactions.share": "Part",
    "transactions.occurrences": "Occurrences",
    "transactions.fraud_simulated": (
        "Cette opération fait partie d'une fraude **simulée** du jeu de démonstration "
        "(scénario : {scenario})."
    ),

    # --- Interface : paiement --------------------------------------------
    "payment.kicker": "Paiement sécurisé",
    "payment.title": "Payer avec une validation dynamique",
    "payment.subtitle": (
        "Chaque opération est notée de 0 à 100 avant validation. Un code à usage unique de "
        "60 secondes est exigé ; un code PIN s'y ajoute lorsque le risque est élevé."
    ),
    "payment.step1_title": "1 · Analyse",
    "payment.step1_text": "Montant, heure, lieu, appareil et rythme sont comparés à vos habitudes.",
    "payment.step2_title": "2 · Code à usage unique",
    "payment.step2_text": "6 chiffres, valables 60 s, usage unique, vérifiés côté serveur (3 tentatives).",
    "payment.step3_title": "3 · Décision",
    "payment.step3_text": "Accepter, demander le PIN ou bloquer — avec des facteurs explicables.",
    "payment.scenarios": "Scénarios de démonstration",
    "payment.scenarios_caption": (
        "Ce sont des formulaires préremplis : c'est le moteur de risque qui décide. "
        "L'heure est simulée pour rendre les résultats reproductibles."
    ),
    "payment.launch_scenario": "Lancer ce scénario",
    "payment.scenario.habituel.title": "Achat habituel",
    "payment.scenario.habituel.description": (
        "Boulangerie à midi, petit montant, appareil de confiance : le paiement passe "
        "après un simple code à usage unique."
    ),
    "payment.scenario.montant.title": "Montant inhabituel",
    "payment.scenario.montant.description": (
        "850 € chez un marchand d'électronique (simulé à 15h) : bien au-dessus de vos habitudes, "
        "le moteur exige la vérification renforcée (code à usage unique + code PIN)."
    ),
    "payment.scenario.appareil.title": "Appareil inconnu",
    "payment.scenario.appareil.description": (
        "679 € depuis un navigateur jamais vu sur le compte (simulé à 10h) : un signal critique "
        "impose la double validation, même si le montant reste plausible."
    ),
    "payment.scenario.voyage.title": "Déplacement impossible",
    "payment.scenario.voyage.description": (
        "Un paiement local est d'abord exécuté (simulé à 21h, dans votre ville), puis un retrait "
        "de 620 € à Lagos deux minutes plus tard : le déplacement est physiquement impossible."
    ),
    "payment.scenario.nuit.title": "Achat nocturne",
    "payment.scenario.nuit.description": (
        "380 € sur un site de jeux d'argent à 3h du matin : heure atypique et catégorie sensible, "
        "deux signaux qui déclenchent la vérification renforcée."
    ),
    "payment.free": "Paiement libre",
    "payment.merchant": "Marchand",
    "payment.category": "Catégorie",
    "payment.channel": "Canal",
    "payment.channel_card": "Carte bancaire",
    "payment.channel_online": "Paiement en ligne",
    "payment.channel_transfer": "Virement",
    "payment.channel_atm": "Retrait DAB",
    "payment.channel_direct_debit": "Prélèvement",
    "payment.hour": "Heure de l'opération",
    "payment.hour_help": "Une heure simulée permet de tester un paiement nocturne sans attendre.",
    "payment.amount": "Montant ({currency})",
    "payment.city": "Ville",
    "payment.device": "Appareil",
    "payment.new_device": "🆕 Nouvel appareil (non vérifié)",
    "payment.balance_available": "Solde disponible : **{balance}** · Téléphone : {phone}",
    "payment.analyze": "Analyser le paiement",
    "payment.merchant_error": "Le nom du marchand doit contenir au moins 2 caractères.",
    "payment.analysis_result": "Résultat de l'analyse",
    "payment.score_line": "Score de risque {score}/100 · Score IA {ai}",
    "payment.risk_label": "Risque : {score}/100",
    "payment.operation_line": "Opération : {amount} vers « {merchant} » à {city}.",
    "payment.see_factors": "Voir les facteurs de risque",
    "payment.blocked": (
        "Paiement bloqué : aucune validation n'est possible. Une alerte de sécurité a été "
        "enregistrée dans votre centre de sécurité."
    ),
    "payment.new_analysis": "Nouvelle analyse",
    "payment.new_payment": "Nouveau paiement",
    "payment.new_balance": "Nouveau solde",
    "payment.challenge_missing": "Demande de paiement introuvable. Relancez l'analyse.",
    "payment.challenge_used": "Ce défi a déjà été utilisé ou annulé. Relancez l'analyse pour un nouveau code.",
    "payment.code_expired": "Le code a expiré. Vous pouvez en demander un nouveau.",
    "payment.otp_title": "Validation par code à usage unique",
    "payment.code_demo": "Code de démonstration",
    "payment.code_demo_note": (
        "En production, ce code serait envoyé par SMS au {phone}. "
        "Valable {ttl} secondes, à usage unique."
    ),
    "payment.code_sent": "Un code a été envoyé au {phone}.",
    "payment.attempts_left": "Tentatives restantes : {left} / {max}.",
    "payment.pin_required": "Vérification renforcée : le code à usage unique **et** votre code PIN sont exigés.",
    "payment.code_label": "Code à 6 chiffres",
    "payment.pin_label": "Code PIN",
    "payment.pin_help": "Code PIN de démonstration : {pin}",
    "payment.validate": "Valider le paiement",
    "payment.new_code": "Obtenir un nouveau code",
    "payment.cancel": "Annuler ce paiement",
    "payment.cancelled": "Paiement annulé. Aucun montant n'a été débité.",
    "payment.countdown": "Temps restant : {seconds} s",
    "payment.countdown_expired": "Code expiré. Demandez un nouveau code pour poursuivre.",
    "payment.checking": "Vérification côté serveur…",
    "payment.pin_missing": "Le code PIN est requis pour cette opération sensible.",
    "payment.atypical_alert": "Cette opération reste atypique pour votre profil : une alerte de sécurité a été créée.",
    "payment.prepayment_done": "Paiement local de préparation exécuté : {amount} à {city}.",
    "payment.prepayment_failed": "Paiement de préparation non exécuté : {message}",
    "payment.prepayment_blocked": "Le paiement de préparation a été bloqué par le moteur de risque.",

    # --- Interface : sécurité & IA ---------------------------------------
    "security.kicker": "Sécurité & intelligence artificielle",
    "security.title": "Votre profil, vos anomalies, vos alertes",
    "security.subtitle": (
        "Le modèle Isolation Forest compare chaque opération à vos habitudes. "
        "{count} alerte(s) non lue(s) pour le moment."
    ),
    "security.tab_profile": "🧠 Profil comportemental",
    "security.tab_anomalies": "🔎 Anomalies",
    "security.tab_alerts": "🔔 Alertes",
    "security.tab_devices": "📱 Appareils",
    "security.tab_audit": "📜 Journal",
    "security.avg_basket": "Panier moyen",
    "security.median": "Médiane",
    "security.monthly_avg": "Dépense mensuelle moy.",
    "security.analyzed": "Opérations analysées",
    "security.range_caption": (
        "Fourchette habituelle : {low} à {high} · heure favorite : {hour} h "
        "· nuit : {night} · en ligne : {online}."
    ),
    "security.hourly": "Répartition horaire",
    "security.hour": "Heure",
    "security.day": "Jour",
    "security.weekday": "Répartition par jour",
    "security.observations": "Observations",
    "security.top_categories": "Principales catégories",
    "security.subscriptions": "Abonnements récurrents",
    "security.no_subscriptions": "Aucun abonnement détecté.",
    "security.recommendations": "Recommandations personnalisées",
    "security.priority": "Priorité {priority}",
    "security.impact": "impact estimé <strong>{amount} / mois</strong>",
    "security.kind_securite": "Sécurité",
    "security.kind_epargne": "Épargne",
    "security.kind_budget": "Budget",
    "security.anomalies_caption": (
        "Opérations les plus atypiques selon l'Isolation Forest (revenus et charges régulières exclus). "
        "Un score proche de 1 signale un comportement très éloigné de vos habitudes."
    ),
    "security.no_anomaly": "Aucune anomalie détectée dans l'historique.",
    "security.anomaly_badge": "Anomalie IA",
    "security.anomaly_scores": "Score IA {ai} · Score risque {risk}/100 · Statut : {status}",
    "security.alerts_count": "**{total} alerte(s)** — {unread} non lue(s)",
    "security.mark_all_read": "Tout marquer comme lu",
    "security.mark_read": "Marquer comme lue",
    "security.no_alert": "Aucune alerte.",
    "security.device_caption": "Un appareil non vérifié ne bloque pas le paiement, mais déclenche une vérification renforcée.",
    "security.device": "Appareil",
    "security.platform": "Plateforme",
    "security.verified": "Vérifié",
    "security.last_used": "Dernière utilisation",
    "security.manage": "Gérer un appareil",
    "security.mark_verified": "Marquer comme vérifié",
    "security.confirm_revoke": "Je confirme la révocation de cet appareil",
    "security.revoke": "Révoquer l'appareil",
    "security.no_device": "Aucun appareil enregistré.",
    "security.audit_caption": "Journal d'audit : connexions, analyses, validations, blocages et actions de sécurité.",
    "security.action": "Action",
    "security.no_audit": "Aucune entrée d'audit.",

    # --- Interface : modèle & données ------------------------------------
    "model.kicker": "Transparence",
    "model.title": "Modèle & données",
    "model.subtitle": "Isolation Forest, variables comportementales, métriques de détection et cycle de vie du modèle.",
    "model.not_trained": "Le modèle n'est pas encore entraîné. Utilisez le bouton de réentraînement ci-dessous.",
    "model.roc_auc": "ROC AUC",
    "model.precision": "Précision",
    "model.recall": "Rappel",
    "model.f1": "F1",
    "model.caption": (
        "Algorithme : {algorithm} · {samples} exemples · {features} variables · "
        "contamination {contamination}."
    ),
    "model.threshold_caption": "Seuil opérationnel : {threshold} (F1 {f1}, rappel {recall}).",
    "model.features": "Variables utilisées par le modèle",
    "model.features_caption": (
        "Les encaissements (salaire, virements reçus), les opérations bloquées et les fraudes "
        "simulées sont exclus de l'apprentissage : le modèle décrit le comportement de dépense réel."
    ),
    "model.eval_title": "Évaluation du moteur de décision complet",
    "model.eval_caption": (
        "Le score d'anomalie seul ne suffit pas : cette évaluation rejoue le moteur complet "
        "(règles + IA) sur toutes les fraudes simulées et un échantillon de paiements légitimes."
    ),
    "model.eval_button": "Lancer l'évaluation (environ 30 secondes)",
    "model.eval_running": "Rejeu du moteur sur l'historique…",
    "model.detection_rate": "Taux de détection",
    "model.friction_rate": "Taux de friction",
    "model.frauds_evaluated": "Fraudes évaluées",
    "model.legit_payments": "Paiements légitimes",
    "model.eval_breakdown": (
        "Fraudes bloquées : {blocked} · escaladées : {escalated} · acceptées : {accepted}. "
        "Légitimes acceptés : {legit} · escaladés : {legit_escalated} · bloqués : {legit_blocked}."
    ),
    "model.eval_scores": "Score moyen des fraudes : {fraud}/100 · score moyen des paiements légitimes : {legit}/100.",
    "model.lifecycle": "Cycle de vie du modèle",
    "model.lifecycle_caption": (
        "En production, l'entraînement serait planifié (par exemple chaque nuit) sur des données "
        "anonymisées. Ici, il est déclenché à la demande pour la démonstration."
    ),
    "model.retrain": "Réentraîner l'Isolation Forest",
    "model.retrain_interval": (
        "Intervalle minimal entre deux réentraînements : {seconds} s — il évite de réentraîner "
        "le modèle pour rien, et empêche deux instances de le faire en même temps."
    ),
    "model.retraining": "Entraînement et re-scoring de l'historique…",
    "model.dataset": "Jeu de données de démonstration",
    "model.users": "Utilisateurs",
    "model.transactions": "Transactions",
    "model.frauds": "Fraudes simulées",
    "model.blocked": "Paiements bloqués",
    "model.storage_note": (
        "Base SQLite créée automatiquement au premier lancement. Sur Streamlit Cloud, le disque "
        "est éphémère : les données sont régénérées à chaque redémarrage. En local, "
        "`python -m app.seed --force` régénère le jeu de démonstration."
    ),
    "model.demo_accounts": "Comptes de démonstration",
    "model.model_store": "Stockage du modèle : {store} · version des variables : {version}.",
    "model.version_mismatch": "Artefact ignoré (version incompatible) : {reason}.",
}


# ---------------------------------------------------------------------------
# Catalogue anglais
# ---------------------------------------------------------------------------
_CATALOG_EN: dict[str, str] = {
    "common.date_at": "{date} at {time}",
    "common.time": "{hours}:{minutes:02d}",
    "common.none": "—",
    "common.yes": "Yes",
    "common.no": "No",
    "common.all": "All",
    "common.current_hour": "Current hour",
    "common.read": "Read",
    "common.unread": "Unread",
    "common.new": "New",
    "common.loading": "Loading…",
    "common.offline_note": "Simulated data — no real payment is processed.",

    "auth.invalid_credentials": "Incorrect email address or password.",
    "auth.locked": (
        "Account temporarily locked after too many attempts. "
        "Try again in {minutes} minute(s)."
    ),
    "auth.inactive": "This account is disabled.",
    "auth.register.conflict": "An account already exists with this email address.",
    "auth.register.pin_invalid": "The PIN code must contain 4 to 6 digits.",
    "auth.register.phone_invalid": "Invalid phone number: use the international format (+33 6 12 34 56 78).",
    "auth.register.currency_invalid": "Unknown currency: use a valid ISO 4217 code (EUR, USD, GBP…).",
    "auth.register.timezone_invalid": "Unknown time zone: use an IANA identifier (Europe/Paris, America/New_York…).",
    "auth.register.locale_invalid": "Invalid language or locale (examples: fr-FR, en-US).",
    "auth.profile.updated": "Your regional preferences have been updated.",
    "auth.token_expired": "Session expired or invalid token.",
    "auth.required": "Authentication required.",

    "decision.accept": "Payment accepted",
    "decision.verify": "Additional verification required",
    "decision.block": "Payment blocked",
    "decision.accept_short": "Accepted — one-time code required",
    "decision.verify_short": "Additional verification — code + PIN",
    "decision.block_short": "Payment blocked",
    "status.completed": "Completed",
    "status.blocked": "Blocked",
    "status.pending": "Pending",
    "status.cancelled": "Cancelled",
    "severity.critique": "Critical",
    "severity.eleve": "High",
    "severity.moyen": "Medium",
    "severity.faible": "Low",
    "kind.fraude": "Fraud",
    "kind.anomalie": "Anomaly",
    "kind.securite": "Security",
    "kind.info": "Information",

    "payment.insufficient_funds": (
        "Payment declined: insufficient funds ({available} available "
        "for {amount} requested)."
    ),
    "payment.account_missing": "No checking account associated with this user.",
    "payment.completed": "Payment of {amount} to “{merchant}” confirmed.",
    "payment.error_code_required": "Enter the 6 digits of the one-time code.",
    "risk.summary.accept": (
        "Risk score {score}/100: this transaction matches your habits. "
        "A one-time code is still required to confirm it."
    ),
    "risk.summary.verify": (
        "Risk score {score}/100. Signals detected: {reasons}. "
        "Confirm with the one-time code AND your PIN."
    ),
    "risk.summary.block": (
        "Payment of {amount} blocked: risk score {score}/100 ({reasons})."
    ),
    "risk.summary.block_hard": "Payment declined for security reasons. {reason}",
    "risk.reasons_default": "unusual behaviour",
    "risk.otp_required": "One-time code required",

    "factor.montant_inhabituel": "Unusual amount",
    "factor.montant_eleve": "High amount",
    "factor.solde_insuffisant": "Insufficient funds",
    "factor.compte_presque_vide": "Balance at risk",
    "factor.heure_inhabituelle": "Unusual time",
    "factor.appareil_inconnu": "Unknown device",
    "factor.localisation_inhabituelle": "Unusual location",
    "factor.zone_inhabituelle": "Unusual geographical zone",
    "factor.velocite_impossible": "Impossible travel",
    "factor.velocite_suspecte": "Very fast travel",
    "factor.frequence_anormale": "Abnormal transaction pace",
    "factor.nouveau_beneficiaire": "New payee",
    "factor.categorie_sensible": "Higher-risk category",
    "factor.canal_inhabituel": "Unusual channel",
    "factor.anomalie_ia": "Behavioural anomaly (AI)",
    "factor.step_up": "Additional verification required",

    "otp.expired": "Code expired ({ttl} s). Restart the payment to get a new code.",
    "otp.consumed": "This code has already been used. Restart the operation to get a new one.",
    "otp.too_many": "Challenge invalidated after too many attempts. Restart the payment for a new code.",
    "otp.invalidated": "Too many attempts: this code has been invalidated for security.",
    "otp.incorrect": "Incorrect code. {remaining} attempt(s) left.",
    "otp.incorrect_final": "Incorrect code. Challenge invalidated after {max} attempts.",
    "otp.pin_required": "PIN code required to confirm this sensitive operation.",
    "otp.pin_incorrect": "Incorrect PIN code. {remaining} attempt(s) left.",
    "otp.pin_final": "Incorrect PIN code. Challenge invalidated after {max} attempts.",
    "otp.validated": "Code validated.",

    # --- Alerts (stored in the user's language) --------------------------
    "alert.failed_validation.title": "Failed validation attempt",
    "alert.failed_validation.message": "An incorrect payment code was entered for the operation to “{merchant}” ({message})",
    "alert.new_device.title": "New device used",
    "alert.new_device.message": (
        "A payment was made from “{device}”, an unverified device. "
        "If this was not you, change your password and PIN code."
    ),
    "alert.blocked.title": "Payment blocked: {merchant} ({amount})",
    "alert.blocked.message": "An operation of {amount} to “{merchant}” was declined. Reasons: {reasons}.",
    "alert.blocked.fallback_reason": "unusual behaviour",
    "alert.anomaly.title": "Unusual operation: {merchant} ({amount})",
    "alert.anomaly.message": (
        "This operation was executed but the AI model considered it unusual for your account. "
        "{reasons}Review it; if you do not recognise it, report it from the security centre."
    ),

    "app.title": "NexPay AI — Banking & secure payment",
    "app.brand_subtitle": "SMART BANKING",
    "app.prototype": "Prototype — simulated payment",
    "nav.navigation": "Navigation",
    "nav.dashboard": "📊 Dashboard",
    "nav.transactions": "🧾 Transactions",
    "nav.payment": "🔐 Secure payment",
    "nav.security": "🛡️ Security & AI",
    "nav.model": "🧠 Model & data",
    "nav.logout": "Log out",
    "nav.otp_ttl": "One-time code: {ttl} s",
    "nav.thresholds": "Thresholds: accept ≤ {accept} · verify ≤ {verify}",
    "nav.unread_alerts": "🔔 {count} unread alert(s)",
    "nav.no_unread": "✅ No unread alerts",
    "nav.language": "Language",
    "nav.region": "Profile & region",

    "login.headline": "Payments, secured by AI.",
    "login.intro": (
        "Every operation is assessed before confirmation: a 0–100 risk score, a 60-second "
        "single-use code, Isolation Forest anomaly detection and personalised advice."
    ),
    "login.feature_otp_title": "🔐 Dynamic authentication",
    "login.feature_otp_text": "6-digit single-use code, valid 60 s, verified server-side.",
    "login.feature_ai_title": "🧠 Artificial intelligence",
    "login.feature_ai_text": "Isolation Forest learns your habits and flags unusual operations.",
    "login.feature_decision_title": "🛡️ Automatic decision",
    "login.feature_decision_text": "Accept, require additional verification or block — with readable reasons.",
    "login.simulation_notice": "Educational prototype — no real payment, no card details requested.",
    "login.title": "Log in",
    "login.prefilled": "Demo credentials are pre-filled — simply click “Log in”.",
    "login.email": "Email address",
    "login.password": "Password",
    "login.submit": "Log in",
    "login.demo_accounts": "Demo accounts",
    "login.demo_caption": (
        "All eight accounts in the dataset share the same password. "
        "The `demo@nexpay.fr` account is the richest (history, simulated frauds)."
    ),

    "region.title": "Profile & region",
    "region.caption": "These preferences only affect display (language, time zone, currency) and your contact details.",
    "region.country": "Country or region",
    "region.city": "City",
    "region.phone": "Phone (international format)",
    "region.timezone": "Time zone",
    "region.currency": "Display currency",
    "region.locale": "Regional format (numbers and dates)",
    "region.save": "Save",
    "region.saved": "Preferences saved (language: {lang}, time zone: {tz}, currency: {currency}).",
    "region.currency_unavailable": (
        "Amounts remain displayed in EUR: no reliable exchange-rate source is configured "
        "(see FX_RATES_JSON). No conversion is invented."
    ),
    "region.currency_converted": "Amounts converted from EUR using the configured source: {source}.",
    "region.simulation_amounts": "Simulated amounts — no real currency is debited.",

    "dashboard.kicker": "Dashboard",
    "dashboard.greeting": "Hello, {name} 👋",
    "dashboard.subtitle": "Overview as of {date}.",
    "dashboard.unread": "{count} security alert(s) to review.",
    "dashboard.kpi.balance": "Checking balance",
    "dashboard.kpi.balance_delta": "{amount} this month",
    "dashboard.kpi.savings": "Savings",
    "dashboard.kpi.spending": "Spending (month)",
    "dashboard.kpi.spending_share": "{pct} of income",
    "dashboard.kpi.income": "Income (month)",
    "dashboard.balance_evolution": "Balance history — last 30 days",
    "dashboard.balance_series": "Balance",
    "dashboard.spending_categories": "Spending by category — 30 days",
    "dashboard.no_spending": "No spending yet for this period.",
    "dashboard.daily_spending": "Spending over the last 7 days",
    "dashboard.daily_series": "Spending",
    "dashboard.subscriptions": "Detected subscriptions",
    "dashboard.subscriptions_total": "Monthly total",
    "dashboard.no_subscriptions": "No recurring subscription detected.",
    "dashboard.recent_operations": "Latest operations",
    "dashboard.recent_alerts": "Recent alerts",
    "dashboard.no_alerts": "No alert at the moment.",
    "dashboard.no_transactions": "No operation.",
    "dashboard.no_account": "No checking account associated.",

    "transactions.kicker": "History",
    "transactions.title": "Transactions",
    "transactions.subtitle": (
        "All your operations, including attempts blocked by the security engine. "
        "A blocked operation never happened: it does not affect your balance."
    ),
    "transactions.period": "Period",
    "transactions.period_7": "7 days",
    "transactions.period_30": "30 days",
    "transactions.period_90": "90 days",
    "transactions.period_year": "1 year",
    "transactions.period_all": "All",
    "transactions.category": "Category",
    "transactions.search": "Search",
    "transactions.search_placeholder": "Merchant, label or city…",
    "transactions.only_flagged": "Flagged",
    "transactions.page_info": "{total} operation(s) — page {page} / {pages}",
    "transactions.none_match": "No operation matches these filters.",
    "transactions.previous": "← Previous",
    "transactions.next": "Next →",
    "transactions.inspect": "Inspect an operation",
    "transactions.choose": "Choose an operation from this page…",
    "transactions.operation": "Operation",
    "transactions.merchant": "Merchant",
    "transactions.date": "Date",
    "transactions.city": "City",
    "transactions.amount": "Amount",
    "transactions.risk": "Risk",
    "transactions.ai_score": "AI score",
    "transactions.status": "Status",
    "transactions.decision": "Decision",
    "transactions.channel": "Channel",
    "transactions.location": "Location",
    "transactions.factors": "Risk factors",
    "transactions.factor": "Factor",
    "transactions.detail": "Detail",
    "transactions.points": "Points",
    "transactions.severity": "Severity",
    "transactions.operations": "Operations",
    "transactions.share": "Share",
    "transactions.occurrences": "Occurrences",
    "transactions.fraud_simulated": (
        "This operation is part of a **simulated** fraud from the demo dataset (scenario: {scenario})."
    ),

    "payment.kicker": "Secure payment",
    "payment.title": "Pay with dynamic validation",
    "payment.subtitle": (
        "Every operation is scored from 0 to 100 before confirmation. A 60-second single-use "
        "code is required; a PIN is added when the risk is high."
    ),
    "payment.step1_title": "1 · Assessment",
    "payment.step1_text": "Amount, time, location, device and pace are compared with your habits.",
    "payment.step2_title": "2 · One-time code",
    "payment.step2_text": "6 digits, valid 60 s, single use, server-side verification (3 attempts).",
    "payment.step3_title": "3 · Decision",
    "payment.step3_text": "Accept, request the PIN or block — with explainable factors.",
    "payment.scenarios": "Demo scenarios",
    "payment.scenarios_caption": (
        "These are pre-filled forms: the risk engine decides. "
        "The time is simulated so results are reproducible."
    ),
    "payment.launch_scenario": "Run this scenario",
    "payment.scenario.habituel.title": "Usual purchase",
    "payment.scenario.habituel.description": (
        "Bakery at noon, small amount, trusted device: the payment goes through after a "
        "single one-time code."
    ),
    "payment.scenario.montant.title": "Unusual amount",
    "payment.scenario.montant.description": (
        "€850 at an electronics merchant (simulated at 3 pm): far above your habits, the engine "
        "requires enhanced verification (one-time code + PIN)."
    ),
    "payment.scenario.appareil.title": "Unknown device",
    "payment.scenario.appareil.description": (
        "€679 from a browser never seen on the account (simulated at 10 am): a critical signal "
        "requires dual validation even though the amount is plausible."
    ),
    "payment.scenario.voyage.title": "Impossible travel",
    "payment.scenario.voyage.description": (
        "A local payment is executed first (simulated at 9 pm, in your city), then a €620 "
        "withdrawal in Lagos two minutes later: the travel is physically impossible."
    ),
    "payment.scenario.nuit.title": "Night purchase",
    "payment.scenario.nuit.description": (
        "€380 on a gambling site at 3 am: unusual hour and sensitive category, two signals "
        "that trigger enhanced verification."
    ),
    "payment.free": "Custom payment",
    "payment.merchant": "Merchant",
    "payment.category": "Category",
    "payment.channel": "Channel",
    "payment.channel_card": "Bank card",
    "payment.channel_online": "Online payment",
    "payment.channel_transfer": "Transfer",
    "payment.channel_atm": "ATM withdrawal",
    "payment.channel_direct_debit": "Direct debit",
    "payment.hour": "Operation time",
    "payment.hour_help": "A simulated time lets you test a night payment without waiting.",
    "payment.amount": "Amount ({currency})",
    "payment.city": "City",
    "payment.device": "Device",
    "payment.new_device": "🆕 New device (unverified)",
    "payment.balance_available": "Available balance: **{balance}** · Phone: {phone}",
    "payment.analyze": "Assess payment",
    "payment.merchant_error": "The merchant name must contain at least 2 characters.",
    "payment.analysis_result": "Assessment result",
    "payment.score_line": "Risk score {score}/100 · AI score {ai}",
    "payment.risk_label": "Risk: {score}/100",
    "payment.operation_line": "Operation: {amount} to “{merchant}” in {city}.",
    "payment.see_factors": "View risk factors",
    "payment.blocked": (
        "Payment blocked: no confirmation is possible. A security alert was "
        "recorded in your security centre."
    ),
    "payment.new_analysis": "New assessment",
    "payment.new_payment": "New payment",
    "payment.new_balance": "New balance",
    "payment.challenge_missing": "Payment request not found. Restart the assessment.",
    "payment.challenge_used": "This challenge was already used or cancelled. Restart for a new code.",
    "payment.code_expired": "The code has expired. You can request a new one.",
    "payment.otp_title": "Confirm with a one-time code",
    "payment.code_demo": "Demo code",
    "payment.code_demo_note": (
        "In production this code would be sent by SMS to {phone}. "
        "Valid {ttl} seconds, single use."
    ),
    "payment.code_sent": "A code was sent to {phone}.",
    "payment.attempts_left": "Attempts left: {left} / {max}.",
    "payment.pin_required": "Additional verification: the one-time code **and** your PIN are required.",
    "payment.code_label": "6-digit code",
    "payment.pin_label": "PIN code",
    "payment.pin_help": "Demo PIN code: {pin}",
    "payment.validate": "Confirm payment",
    "payment.new_code": "Get a new code",
    "payment.cancel": "Cancel this payment",
    "payment.cancelled": "Payment cancelled. No amount was debited.",
    "payment.countdown": "Time left: {seconds} s",
    "payment.countdown_expired": "Code expired. Request a new code to continue.",
    "payment.checking": "Server-side verification…",
    "payment.pin_missing": "The PIN code is required for this sensitive operation.",
    "payment.atypical_alert": "This operation remains unusual for your profile: a security alert was created.",
    "payment.prepayment_done": "Local setup payment executed: {amount} in {city}.",
    "payment.prepayment_failed": "Setup payment not executed: {message}",
    "payment.prepayment_blocked": "The setup payment was blocked by the risk engine.",

    "security.kicker": "Security & artificial intelligence",
    "security.title": "Your profile, anomalies and alerts",
    "security.subtitle": (
        "The Isolation Forest model compares every operation with your habits. "
        "{count} unread alert(s) at the moment."
    ),
    "security.tab_profile": "🧠 Behavioural profile",
    "security.tab_anomalies": "🔎 Anomalies",
    "security.tab_alerts": "🔔 Alerts",
    "security.tab_devices": "📱 Devices",
    "security.tab_audit": "📜 Audit log",
    "security.avg_basket": "Average basket",
    "security.median": "Median",
    "security.monthly_avg": "Avg. monthly spending",
    "security.analyzed": "Operations analysed",
    "security.range_caption": (
        "Usual range: {low} to {high} · favourite hour: {hour} h "
        "· night: {night} · online: {online}."
    ),
    "security.hourly": "Hourly distribution",
    "security.hour": "Hour",
    "security.day": "Day",
    "security.weekday": "Weekday distribution",
    "security.observations": "Observations",
    "security.top_categories": "Top categories",
    "security.subscriptions": "Recurring subscriptions",
    "security.no_subscriptions": "No subscription detected.",
    "security.recommendations": "Personalised recommendations",
    "security.priority": "Priority {priority}",
    "security.impact": "estimated impact <strong>{amount} / month</strong>",
    "security.kind_securite": "Security",
    "security.kind_epargne": "Savings",
    "security.kind_budget": "Budget",
    "security.anomalies_caption": (
        "The most unusual operations according to Isolation Forest (income and regular charges excluded). "
        "A score close to 1 signals behaviour far from your habits."
    ),
    "security.no_anomaly": "No anomaly detected in the history.",
    "security.anomaly_badge": "AI anomaly",
    "security.anomaly_scores": "AI score {ai} · Risk score {risk}/100 · Status: {status}",
    "security.alerts_count": "**{total} alert(s)** — {unread} unread",
    "security.mark_all_read": "Mark all as read",
    "security.mark_read": "Mark as read",
    "security.no_alert": "No alert.",
    "security.device_caption": "An unverified device does not block the payment, but triggers additional verification.",
    "security.device": "Device",
    "security.platform": "Platform",
    "security.verified": "Verified",
    "security.last_used": "Last used",
    "security.manage": "Manage a device",
    "security.mark_verified": "Mark as verified",
    "security.confirm_revoke": "I confirm the revocation of this device",
    "security.revoke": "Revoke device",
    "security.no_device": "No registered device.",
    "security.audit_caption": "Audit log: logins, assessments, confirmations, blocks and security actions.",
    "security.action": "Action",
    "security.no_audit": "No audit entry.",

    "model.kicker": "Transparency",
    "model.title": "Model & data",
    "model.subtitle": "Isolation Forest, behavioural features, detection metrics and model lifecycle.",
    "model.not_trained": "The model is not trained yet. Use the retraining button below.",
    "model.roc_auc": "ROC AUC",
    "model.precision": "Precision",
    "model.recall": "Recall",
    "model.f1": "F1",
    "model.caption": (
        "Algorithm: {algorithm} · {samples} samples · {features} features · "
        "contamination {contamination}."
    ),
    "model.threshold_caption": "Operating threshold: {threshold} (F1 {f1}, recall {recall}).",
    "model.features": "Features used by the model",
    "model.features_caption": (
        "Incoming funds (salary, received transfers), blocked operations and simulated frauds "
        "are excluded from training: the model describes real spending behaviour."
    ),
    "model.eval_title": "Full decision engine evaluation",
    "model.eval_caption": (
        "The anomaly score alone is not enough: this evaluation replays the full engine "
        "(rules + AI) on every simulated fraud and a sample of legitimate payments."
    ),
    "model.eval_button": "Run the evaluation (about 30 seconds)",
    "model.eval_running": "Replaying the engine over the history…",
    "model.detection_rate": "Detection rate",
    "model.friction_rate": "Friction rate",
    "model.frauds_evaluated": "Frauds evaluated",
    "model.legit_payments": "Legitimate payments",
    "model.eval_breakdown": (
        "Frauds blocked: {blocked} · escalated: {escalated} · accepted: {accepted}. "
        "Legitimate accepted: {legit} · escalated: {legit_escalated} · blocked: {legit_blocked}."
    ),
    "model.eval_scores": "Average fraud score: {fraud}/100 · average legitimate payment score: {legit}/100.",
    "model.lifecycle": "Model lifecycle",
    "model.lifecycle_caption": (
        "In production, training would be scheduled (for instance nightly) on anonymised data. "
        "Here it is triggered on demand for the demonstration."
    ),
    "model.retrain": "Retrain the Isolation Forest",
    "model.retrain_interval": (
        "Minimum interval between retrains: {seconds} s — it avoids retraining the model "
        "for nothing and prevents two instances from training at the same time."
    ),
    "model.retraining": "Training and rescoring the history…",
    "model.dataset": "Demo dataset",
    "model.users": "Users",
    "model.transactions": "Transactions",
    "model.frauds": "Simulated frauds",
    "model.blocked": "Blocked payments",
    "model.storage_note": (
        "SQLite database created automatically on first launch. On Streamlit Cloud the disk "
        "is ephemeral: data is regenerated on each restart. Locally, "
        "`python -m app.seed --force` regenerates the demo dataset."
    ),
    "model.demo_accounts": "Demo accounts",
    "model.model_store": "Model storage: {store} · feature version: {version}.",
    "model.version_mismatch": "Artifact ignored (incompatible version): {reason}.",
}


CATALOGS: dict[str, dict[str, str]] = {
    "fr": _CATALOG_FR,
    "en": _CATALOG_EN,
}

__all__ = [
    "CATALOGS",
    "DEFAULT_LANGUAGE",
    "LANGUAGE_LABELS",
    "catalog_keys",
    "language_from_locale",
    "normalize_language",
    "t",
]
