"""
NexPay AI — interface Streamlit (démonstration en ligne).

Cette interface réutilise **exactement** les services de l'application FastAPI :
moteur de risque, codes à usage unique, modèle Isolation Forest, profil
comportemental, alertes et recommandations. Aucune règle métier n'est dupliquée.

Lancement local
---------------
    streamlit run streamlit_app.py

Déploiement Streamlit Community Cloud
-------------------------------------
    Main file path : streamlit_app.py

La base SQLite et le modèle sont créés automatiquement au premier lancement
(voir `app/bootstrap.py`). Sur Streamlit Cloud, le disque est éphémère : les
données de démonstration sont régénérées à chaque redémarrage de l'application.
"""

from __future__ import annotations

import html
import math
import secrets
from contextlib import contextmanager
from datetime import datetime, timedelta

import pandas as pd
import streamlit as st
from sqlalchemy import func, select

from app.bootstrap import ensure_ready
from app.config import settings
from app.database import SessionLocal, now_utc
from app.ml.anomaly import explain, model_card, train_detector
from app.ml.features import build_features, context_from_transaction
from app.models import Alert, AuditLog, Device, Transaction, User
from app.security import verify_password
from app.services import payment_flow
from app.services.alerts import log_action, mark_alert_read, unread_alert_count
from app.services.behavior import category_breakdown, daily_series, get_profile, to_local
from app.services.ledger import (
    accounts_of,
    balance_history,
    completed_query,
    fetch_transactions,
    flagged_transactions,
    month_start_local,
    period_totals,
)
from app.services.otp import attempts_left, seconds_remaining
from app.services.recommendations import build_recommendations
from app.services.risk_engine import evaluate_decisions

# ---------------------------------------------------------------------------
# Constantes d'affichage
# ---------------------------------------------------------------------------
DEMO_EMAIL = "demo@nexpay.fr"
DEMO_PASSWORD = "Demo1234!"
DEMO_PIN = "4321"

DEMO_ACCOUNTS = [
    ("demo@nexpay.fr", "Camille Bernard", "compte principal — fraudes simulées"),
    ("yassine@nexpay.fr", "Yassine El Amrani", "profil du soir"),
    ("lea@nexpay.fr", "Léa Moreau", "profil du jour"),
]

DECISION_STYLE = {
    "accept": ("Accepté — code à usage unique requis", "nx-accept"),
    "verify": ("Vérification renforcée — code + PIN", "nx-verify"),
    "block": ("Paiement bloqué", "nx-block"),
}

RECOMMENDATION_STYLE = {
    "securite": ("Sécurité", "nx-verify"),
    "epargne": ("Épargne", "nx-accept"),
    "budget": ("Budget", "nx-info"),
}

ALERT_SEVERITY_STYLE = {
    "critique": ("Critique", "nx-block"),
    "eleve": ("Élevé", "nx-verify"),
    "moyen": ("Moyen", "nx-info"),
    "faible": ("Faible", "nx-neutral"),
}

STATUS_FR = {
    "completed": "Exécutée",
    "blocked": "Bloquée",
    "pending": "En attente",
    "cancelled": "Annulée",
}

DECISION_FR = {"accept": "Accepté", "verify": "Vérifié", "block": "Bloqué"}

CHANNEL_FR = {
    "carte": "Carte bancaire",
    "en_ligne": "Paiement en ligne",
    "virement": "Virement",
    "retrait": "Retrait DAB",
}

WEEKDAYS_FR = ["Lundi", "Mardi", "Mercredi", "Jeudi", "Vendredi", "Samedi", "Dimanche"]

# ---------------------------------------------------------------------------
# Configuration de la page et style
# ---------------------------------------------------------------------------
st.set_page_config(
    page_title="NexPay AI — Banque & paiement sécurisé",
    page_icon="🛡️",
    layout="wide",
    initial_sidebar_state="expanded",
)

STYLE = """
<style>
  html, body, [class*="st-"] {
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
  }
  .block-container { padding-top: 2rem; padding-bottom: 3.5rem; max-width: 1180px; }
  [data-testid="stSidebar"] { border-right: 1px solid rgba(148, 163, 184, .14); }
  [data-testid="stSidebar"] .block-container { padding-top: 1.4rem; }

  .nx-brand { display: flex; align-items: center; gap: .65rem; margin-bottom: .2rem; }
  .nx-logo {
    display: inline-flex; align-items: center; justify-content: center;
    width: 40px; height: 40px; border-radius: 13px; flex: 0 0 40px;
    background: linear-gradient(135deg, #22d3ee, #6366f1);
    color: #04121f; font-weight: 900; font-size: 1.3rem;
    box-shadow: 0 8px 24px rgba(34, 211, 238, .25);
  }
  .nx-brand-title { font-size: 1.35rem; font-weight: 800; letter-spacing: -.03em; line-height: 1.05; }
  .nx-brand-title span { color: #22d3ee; }
  .nx-brand small {
    display: block; color: #7c8ba1; font-size: .62rem; font-weight: 700;
    letter-spacing: .16em; margin-top: .2rem;
  }

  .nx-hero {
    padding: 1.35rem 1.55rem; border: 1px solid rgba(148, 163, 184, .16);
    border-radius: 20px; margin-bottom: 1.25rem;
    background:
      radial-gradient(1100px 260px at 0% 0%, rgba(34, 211, 238, .13), transparent 60%),
      radial-gradient(900px 260px at 100% 0%, rgba(99, 102, 241, .12), transparent 60%),
      rgba(17, 28, 51, .55);
  }
  .nx-kicker {
    color: #22d3ee; font-size: .7rem; font-weight: 800;
    letter-spacing: .18em; text-transform: uppercase;
  }
  .nx-hero h1 { font-size: 1.85rem; margin: .3rem 0 .25rem; letter-spacing: -.035em; }
  .nx-hero p { margin: 0; color: #9fb0c8; font-size: .92rem; max-width: 860px; }

  .nx-pill {
    display: inline-block; padding: .2rem .62rem; border-radius: 999px;
    font-size: .7rem; font-weight: 700; letter-spacing: .02em; white-space: nowrap;
  }
  .nx-accept { background: rgba(74, 222, 128, .12); color: #4ade80; border: 1px solid rgba(74, 222, 128, .35); }
  .nx-verify { background: rgba(251, 191, 36, .12); color: #fbbf24; border: 1px solid rgba(251, 191, 36, .35); }
  .nx-block { background: rgba(248, 113, 113, .12); color: #f87171; border: 1px solid rgba(248, 113, 113, .35); }
  .nx-info { background: rgba(34, 211, 238, .10); color: #67e8f9; border: 1px solid rgba(34, 211, 238, .30); }
  .nx-neutral { background: rgba(148, 163, 184, .12); color: #cbd5e1; border: 1px solid rgba(148, 163, 184, .25); }

  .nx-card {
    border: 1px solid rgba(148, 163, 184, .16); border-radius: 16px;
    padding: .9rem 1.05rem; background: rgba(17, 28, 51, .5); margin-bottom: .55rem;
  }
  .nx-muted { color: #8fa2bb; font-size: .8rem; }
  .nx-big { font-size: 1.7rem; font-weight: 800; letter-spacing: -.03em; }
  .nx-code {
    font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
    font-size: 1.8rem; font-weight: 800; letter-spacing: .32em; color: #67e8f9;
  }

  div[data-testid="stMetric"] {
    background: rgba(17, 28, 51, .55); border: 1px solid rgba(148, 163, 184, .16);
    border-radius: 16px; padding: .75rem 1rem;
  }
  div[data-testid="stMetricLabel"] p {
    color: #9fb0c8; font-size: .74rem; text-transform: uppercase;
    letter-spacing: .07em; font-weight: 700;
  }
  div[data-testid="stMetricValue"] { font-weight: 800; letter-spacing: -.02em; font-size: 1.5rem; }
  div[data-testid="stMetricDelta"] { font-size: .72rem; }

  .nx-kpi-grid {
    display: grid; grid-template-columns: repeat(auto-fit, minmax(165px, 1fr));
    gap: .75rem; margin: .15rem 0 1.1rem;
  }
  .nx-kpi {
    border: 1px solid rgba(148, 163, 184, .16); border-radius: 16px;
    padding: .8rem 1rem; background: rgba(17, 28, 51, .55); min-width: 0;
  }
  .nx-kpi-label {
    color: #9fb0c8; font-size: .67rem; text-transform: uppercase;
    letter-spacing: .07em; font-weight: 700; white-space: nowrap;
    overflow: hidden; text-overflow: ellipsis;
  }
  .nx-kpi-value {
    font-size: 1.45rem; font-weight: 800; letter-spacing: -.03em;
    margin-top: .18rem; white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
  }
  .nx-kpi-delta { font-size: .72rem; margin-top: .12rem; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .nx-up { color: #4ade80; }
  .nx-down { color: #f87171; }
  .nx-flat { color: #8fa2bb; }

  .nx-cat-row { display: flex; justify-content: space-between; gap: 1rem; font-size: .86rem; margin-top: .55rem; }
  .nx-cat-track { height: 7px; border-radius: 999px; background: rgba(148, 163, 184, .14); overflow: hidden; margin-top: .28rem; }
  .nx-cat-fill { height: 100%; border-radius: 999px; background: linear-gradient(90deg, #22d3ee, #6366f1); }

  .stTabs [data-baseweb="tab-list"] { gap: 6px; flex-wrap: wrap; }
  .stTabs [data-baseweb="tab"] { padding: .35rem .9rem; border-radius: 10px; }
  .stButton button { border-radius: 10px; font-weight: 600; }
</style>
"""


# ---------------------------------------------------------------------------
# Amorçage (une seule fois par processus Streamlit)
# ---------------------------------------------------------------------------
@st.cache_resource(show_spinner=False)
def _bootstrap() -> dict:
    """Crée la base et le modèle au premier lancement (idempotent)."""
    return ensure_ready(verbose=False)


@contextmanager
def _db_session():
    """Fournit une session SQLAlchemy propre à chaque exécution du script."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Petits utilitaires d'affichage
# ---------------------------------------------------------------------------
def _euro(value: float | None) -> str:
    """Montant au format français : 2 502,69 €."""
    amount = float(value or 0.0)
    return f"{amount:,.2f} €".replace(",", "\u202f").replace(".", ",")


def _signed_euro(value: float) -> str:
    """Montant signé : +780,00 € / −120,50 €."""
    if value < 0:
        return "−" + _euro(abs(value))
    return "+" + _euro(value)


def _pct(value: float | None, digits: int = 1) -> str:
    text = f"{float(value or 0.0) * 100:.{digits}f} %"
    return text.replace(".", ",")


def _date(value: datetime | None, with_time: bool = True) -> str:
    if value is None:
        return "—"
    local = to_local(value)
    return local.strftime("%d/%m/%Y à %H:%M") if with_time else local.strftime("%d/%m/%Y")


def _badge(label: str, css_class: str) -> str:
    return f'<span class="nx-pill {css_class}">{html.escape(label)}</span>'


def _kpis(items: list[dict]) -> None:
    """Cartes d'indicateurs responsives (grille qui s'adapte à la largeur)."""
    cards = []
    for item in items:
        delta = item.get("delta")
        if delta:
            tone = item.get("tone", "flat")
            arrow = {"up": "↑ ", "down": "↓ ", "flat": ""}.get(tone, "")
            delta_html = f'<div class="nx-kpi-delta nx-{tone}">{arrow}{html.escape(str(delta))}</div>'
        else:
            delta_html = '<div class="nx-kpi-delta">&nbsp;</div>'
        cards.append(
            f'<div class="nx-kpi"><div class="nx-kpi-label">{html.escape(item["label"])}</div>'
            f'<div class="nx-kpi-value">{html.escape(str(item["value"]))}</div>{delta_html}</div>'
        )
    st.markdown(f'<div class="nx-kpi-grid">{"".join(cards)}</div>', unsafe_allow_html=True)


def _hero(kicker: str, title: str, subtitle: str) -> None:
    st.markdown(
        f'<div class="nx-hero"><div class="nx-kicker">{html.escape(kicker)}</div>'
        f"<h1>{title}</h1><p>{subtitle}</p></div>",
        unsafe_allow_html=True,
    )


def _brand() -> str:
    return (
        '<div class="nx-brand"><span class="nx-logo">N</span>'
        '<div class="nx-brand-title">NexPay <span>AI</span><small>BANQUE INTELLIGENTE</small></div></div>'
    )


def _current_user(db) -> User | None:
    user_id = st.session_state.get("user_id")
    if not user_id:
        return None
    user = db.get(User, user_id)
    if user is None or not user.is_active:
        st.session_state.pop("user_id", None)
        return None
    return user


# ---------------------------------------------------------------------------
# Connexion
# ---------------------------------------------------------------------------
def _render_login(db) -> None:
    left, right = st.columns([1.15, 1], gap="large")

    with left:
        st.markdown(_brand(), unsafe_allow_html=True)
        st.markdown(
            '<h1 style="font-size:2.4rem;letter-spacing:-.04em;margin:.9rem 0 .4rem;">'
            "Le paiement, sécurisé par l'IA.</h1>",
            unsafe_allow_html=True,
        )
        st.markdown(
            '<p style="color:#9fb0c8;font-size:1.02rem;max-width:520px;">'
            "Chaque opération est analysée avant validation : score de risque de 0 à 100, "
            "code à usage unique de 60 secondes, détection d'anomalies par Isolation Forest "
            "et conseils personnalisés.</p>",
            unsafe_allow_html=True,
        )
        st.markdown(
            """
<div class="nx-card" style="margin-top:1rem;">
  <strong>🔐 Authentification dynamique</strong>
  <div class="nx-muted">Code à 6 chiffres, à usage unique, valable 60 s et vérifié côté serveur.</div>
</div>
<div class="nx-card">
  <strong>🧠 Intelligence artificielle</strong>
  <div class="nx-muted">Isolation Forest apprend vos habitudes et signale les opérations atypiques.</div>
</div>
<div class="nx-card">
  <strong>🛡️ Décision automatique</strong>
  <div class="nx-muted">Accepter, demander une vérification renforcée ou bloquer — avec des motifs lisibles.</div>
</div>
            """,
            unsafe_allow_html=True,
        )
        st.caption("Prototype pédagogique — aucun paiement réel, aucune donnée de carte demandée.")

    with right:
        with st.container(border=True):
            st.markdown("### Connexion")
            st.caption(
                "Identifiants de démonstration préremplis — cliquez simplement sur « Se connecter »."
            )

            with st.form("login_form"):
                email = st.text_input(
                    "Adresse e-mail",
                    value=DEMO_EMAIL,
                    placeholder="vous@exemple.fr",
                )
                password = st.text_input(
                    "Mot de passe",
                    type="password",
                    value=DEMO_PASSWORD,
                    placeholder="••••••••",
                )
                submitted = st.form_submit_button("Se connecter", type="primary", use_container_width=True)

            if submitted:
                normalized = email.strip().lower()
                user = db.scalar(select(User).where(User.email == normalized))

                if user and user.locked_until and user.locked_until > now_utc():
                    remaining = int((user.locked_until - now_utc()).total_seconds() // 60) + 1
                    log_action(
                        db,
                        action="connexion",
                        user_id=user.id,
                        status="bloque",
                        detail=f"Compte verrouillé ({remaining} min).",
                        ip_address="streamlit",
                    )
                    st.error(
                        "Compte temporairement verrouillé après trop de tentatives. "
                        f"Réessayez dans {remaining} minute(s)."
                    )
                elif user is None or not verify_password(password, user.password_hash):
                    if user is not None:
                        user.failed_attempts = (user.failed_attempts or 0) + 1
                        if user.failed_attempts >= settings.login_max_attempts:
                            user.locked_until = now_utc() + timedelta(minutes=settings.login_lockout_minutes)
                            user.failed_attempts = 0
                            detail = f"Compte verrouillé pour {settings.login_lockout_minutes} minutes."
                        else:
                            detail = f"Échec de connexion ({user.failed_attempts}/{settings.login_max_attempts})."
                        db.commit()
                        log_action(db, "connexion", user.id, "echec", detail, "streamlit")
                    else:
                        log_action(
                            db,
                            "connexion",
                            None,
                            "echec",
                            f"Adresse inconnue : {normalized}",
                            "streamlit",
                        )
                    st.error("Adresse e-mail ou mot de passe incorrect.")
                elif not user.is_active:
                    st.error("Ce compte est désactivé.")
                else:
                    user.failed_attempts = 0
                    user.locked_until = None
                    user.last_login_at = now_utc()
                    db.commit()
                    log_action(
                        db, "connexion", user.id, "ok", "Connexion réussie (Streamlit).", "streamlit"
                    )
                    st.session_state["user_id"] = user.id
                    st.session_state.pop("prefill_password", None)
                    st.rerun()

            with st.expander("Comptes de démonstration"):
                st.markdown(
                    f"**Mot de passe :** `{DEMO_PASSWORD}` — **Code PIN :** `{DEMO_PIN}`\n\n"
                    + "\n".join(f"- `{mail}` — {name} ({note})" for mail, name, note in DEMO_ACCOUNTS)
                )
                st.caption(
                    "Les huit comptes du jeu de données partagent le même mot de passe. "
                    "Le compte `demo@nexpay.fr` est le plus riche (historique, fraudes simulées)."
                )


# ---------------------------------------------------------------------------
# Coquille applicative (barre latérale + navigation)
# ---------------------------------------------------------------------------
def _render_shell(db, user: User) -> None:
    unread = unread_alert_count(db, user.id)

    with st.sidebar:
        st.markdown(_brand(), unsafe_allow_html=True)
        st.caption("Prototype — paiement simulé")
        st.markdown("---")
        st.markdown(f"**{html.escape(user.full_name)}**")
        st.caption(user.email)
        if unread:
            st.warning(f"🔔 {unread} alerte(s) non lue(s)")
        else:
            st.caption("✅ Aucune alerte non lue")
        st.markdown("---")

        pages = {
            "📊 Tableau de bord": _page_dashboard,
            "🧾 Transactions": _page_transactions,
            "🔐 Paiement sécurisé": _page_payment,
            "🛡️ Sécurité & IA": _page_security,
            "🧠 Modèle & données": _page_model,
        }
        choice = st.radio("Navigation", list(pages), label_visibility="collapsed")
        st.markdown("---")
        st.caption(f"Code à usage unique : {settings.otp_ttl_seconds} s")
        st.caption(f"Seuils : accept ≤ {settings.risk_accept_max} · verify ≤ {settings.risk_verify_max}")
        if st.button("Se déconnecter", use_container_width=True):
            for key in (
                "user_id",
                "payment_quote",
                "payment_meta",
                "payment_feedback",
                "last_payment",
            ):
                st.session_state.pop(key, None)
            st.rerun()

    pages[choice](db, user)


# ---------------------------------------------------------------------------
# Page : tableau de bord
# ---------------------------------------------------------------------------
def _page_dashboard(db, user: User) -> None:
    checking, savings = accounts_of(db, user)
    checking_balance = float(checking.balance if checking else 0.0)
    savings_balance = float(savings.balance if savings else 0.0)
    profile = get_profile(db, user)

    month_spending, month_income = period_totals(db, user, month_start_local())
    month_net = round(month_income - month_spending, 2)
    unread = unread_alert_count(db, user.id)

    subtitle = f"Vue d'ensemble au {_date(now_utc(), with_time=False)}."
    if unread:
        subtitle += f" {unread} alerte(s) de sécurité à consulter."
    _hero("Tableau de bord", f"Bonjour, {html.escape(user.full_name.split()[0])} 👋", subtitle)

    # --- Indicateurs clés -------------------------------------------------
    _kpis(
        [
            {
                "label": "Solde courant",
                "value": _euro(checking_balance),
                "delta": f"{_signed_euro(month_net)} ce mois",
                "tone": "up" if month_net >= 0 else "down",
            },
            {"label": "Épargne", "value": _euro(savings_balance)},
            {
                "label": "Dépenses (mois)",
                "value": _euro(month_spending),
                "delta": (
                    f"{_pct(month_spending / month_income)} des revenus"
                    if month_income
                    else None
                ),
                "tone": "flat",
            },
            {"label": "Revenus (mois)", "value": _euro(month_income)},
        ]
    )

    st.markdown("")

    # --- Fenêtre glissante de 30 jours -----------------------------------
    window_transactions = list(
        db.scalars(
            completed_query(user).where(Transaction.ts >= now_utc() - timedelta(days=30))
        ).all()
    )
    categories = category_breakdown(window_transactions)
    daily = daily_series(window_transactions, days=7)

    left, right = st.columns([1.45, 1])

    with left:
        st.markdown("##### Évolution du solde — 30 derniers jours")
        if checking:
            history = balance_history(db, checking, days=30)
            dates = [
                (to_local(now_utc()).date() - timedelta(days=29 - index)).strftime("%d/%m")
                for index in range(30)
            ]
            st.line_chart(
                pd.DataFrame({"Date": dates, "Solde (€)": history}),
                x="Date",
                y="Solde (€)",
                height=250,
            )
        else:
            st.info("Aucun compte courant associé.")

    with right:
        st.markdown("##### Dépenses par catégorie — 30 jours")
        if categories:
            max_total = max(row["total"] for row in categories[:6]) or 1.0
            for row in categories[:6]:
                width = 100.0 * row["total"] / max_total
                st.markdown(
                    f'<div class="nx-cat-row"><span>{html.escape(row["category"])}</span>'
                    f'<strong>{_euro(row["total"])}</strong></div>'
                    f'<div class="nx-cat-track"><div class="nx-cat-fill" style="width:{width:.1f}%"></div></div>',
                    unsafe_allow_html=True,
                )
        else:
            st.caption("Pas encore de dépenses sur la période.")

    st.markdown("")

    # --- Série quotidienne et abonnements --------------------------------
    left, right = st.columns([1.45, 1])
    with left:
        st.markdown("##### Dépenses des 7 derniers jours")
        daily_frame = pd.DataFrame(
            {
                "Date": [row["date"][5:].replace("-", "/") for row in daily],
                "Dépenses (€)": [row["debit"] for row in daily],
            }
        )
        st.bar_chart(daily_frame, x="Date", y="Dépenses (€)", height=220)

    with right:
        st.markdown("##### Abonnements détectés")
        subscriptions = profile.get("subscriptions") or []
        if subscriptions:
            total_subscriptions = sum(row["amount"] for row in subscriptions)
            st.metric("Total mensuel", _euro(total_subscriptions))
            for row in subscriptions[:5]:
                st.markdown(
                    f'<div class="nx-cat-row"><span>{html.escape(row["merchant"])}</span>'
                    f'<strong>{_euro(row["amount"])}</strong></div>',
                    unsafe_allow_html=True,
                )
        else:
            st.caption("Aucun abonnement récurrent identifié.")

    st.markdown("")

    # --- Dernières opérations et alertes ---------------------------------
    left, right = st.columns([1.55, 1])
    with left:
        st.markdown("##### Dernières opérations")
        recent = list(
            db.scalars(completed_query(user).order_by(Transaction.ts.desc()).limit(8)).all()
        )
        if recent:
            frame = pd.DataFrame(
                [
                    {
                        "Date": _date(txn.ts),
                        "Marchand": txn.merchant,
                        "Catégorie": txn.category,
                        "Montant": _signed_euro(txn.amount),
                        "Statut": STATUS_FR.get(txn.status, txn.status),
                    }
                    for txn in recent
                ]
            )
            st.dataframe(frame, hide_index=True, use_container_width=True)
        else:
            st.caption("Aucune opération.")

    with right:
        st.markdown("##### Alertes récentes")
        alerts = list(
            db.scalars(
                select(Alert)
                .where(Alert.user_id == user.id)
                .order_by(Alert.created_at.desc())
                .limit(4)
            ).all()
        )
        if alerts:
            for alert in alerts:
                severity, severity_css = ALERT_SEVERITY_STYLE.get(
                    alert.severity, (alert.severity, "nx-neutral")
                )
                unread_marker = (
                    '<span class="nx-pill nx-info" style="margin-left:.35rem;">Non lue</span>'
                    if alert.read_at is None
                    else ""
                )
                st.markdown(
                    f'<div class="nx-card">{_badge(severity, severity_css)} '
                    f'<strong>{html.escape(alert.title)}</strong>{unread_marker}'
                    f'<div class="nx-muted">{_date(alert.created_at)}</div>'
                    f'<div style="font-size:.86rem;color:#c7d3e4;margin-top:.25rem;">'
                    f"{html.escape(alert.message)}</div></div>",
                    unsafe_allow_html=True,
                )
        else:
            st.caption("Aucune alerte pour le moment.")


# ---------------------------------------------------------------------------
# Page : transactions
# ---------------------------------------------------------------------------
def _page_transactions(db, user: User) -> None:
    _hero(
        "Historique",
        "Transactions",
        "Toutes vos opérations, y compris les tentatives bloquées par le moteur de sécurité. "
        "Une opération bloquée n'a pas eu lieu : elle n'affecte pas votre solde.",
    )

    categories = [
        row
        for row in db.scalars(
            select(Transaction.category)
            .where(Transaction.user_id == user.id)
            .distinct()
            .order_by(Transaction.category)
        ).all()
    ]

    filter_col1, filter_col2, filter_col3, filter_col4 = st.columns([1, 1, 1.5, 1])
    with filter_col1:
        period_label = st.selectbox("Période", ["30 jours", "7 jours", "90 jours", "1 an", "Tout"])
    with filter_col2:
        category = st.selectbox("Catégorie", ["Toutes"] + categories)
    with filter_col3:
        search = st.text_input("Rechercher", placeholder="Marchand, libellé ou ville…")
    with filter_col4:
        only_flagged = st.checkbox("Signalées")

    period_days = {
        "7 jours": 7,
        "30 jours": 30,
        "90 jours": 90,
        "1 an": 365,
        "Tout": None,
    }[period_label]

    # Toute modification de filtre ramène à la première page.
    signature = (period_days, category, search.strip(), only_flagged)
    if st.session_state.get("txn_signature") != signature:
        st.session_state["txn_signature"] = signature
        st.session_state["txn_page"] = 0

    page = int(st.session_state.get("txn_page", 0))
    limit = 25
    items, total = fetch_transactions(
        db,
        user,
        days=period_days,
        category=None if category == "Toutes" else category,
        search=search.strip() or None,
        only_flagged=only_flagged,
        limit=limit,
        offset=page * limit,
    )
    page_count = max(1, math.ceil(total / limit))
    page = min(page, page_count - 1)
    st.session_state["txn_page"] = page

    st.caption(f"{total} opération(s) — page {page + 1} / {page_count}")

    if items:
        frame = pd.DataFrame(
            [
                {
                    "Date": _date(txn.ts),
                    "Marchand": txn.merchant,
                    "Catégorie": txn.category,
                    "Ville": txn.city,
                    "Montant": _signed_euro(txn.amount),
                    "Risque": int(round(float(txn.risk_score or 0.0))),
                    "Score IA": float(txn.anomaly_score or 0.0),
                    "Statut": STATUS_FR.get(txn.status, txn.status),
                    "Décision": DECISION_FR.get(txn.decision, txn.decision),
                }
                for txn in items
            ]
        )
        st.dataframe(
            frame,
            hide_index=True,
            use_container_width=True,
            column_config={
                "Risque": st.column_config.ProgressColumn(
                    "Risque", min_value=0, max_value=100, format="%d"
                ),
                "Score IA": st.column_config.NumberColumn(format="%.2f"),
            },
        )
    else:
        st.info("Aucune opération ne correspond à ces filtres.")

    navigation_left, navigation_middle, navigation_right = st.columns([1, 2, 1])
    if navigation_left.button("← Précédent", disabled=page <= 0, use_container_width=True):
        st.session_state["txn_page"] = page - 1
        st.rerun()
    if navigation_right.button(
        "Suivant →", disabled=page >= page_count - 1, use_container_width=True
    ):
        st.session_state["txn_page"] = page + 1
        st.rerun()

    # --- Détail d'une opération ------------------------------------------
    if items:
        st.markdown("---")
        st.markdown("##### Inspecter une opération")
        options = {
            f"{_date(txn.ts)} — {txn.merchant} — {_euro(txn.amount)}": txn.id for txn in items
        }
        selected = st.selectbox(
            "Opération",
            list(options),
            index=None,
            placeholder="Choisir une opération de cette page…",
        )
        if selected:
            transaction = db.get(Transaction, options[selected])
            if transaction is not None and transaction.user_id == user.id:
                meta = transaction.meta or {}
                _kpis(
                    [
                        {"label": "Montant", "value": _euro(abs(transaction.amount))},
                        {
                            "label": "Score de risque",
                            "value": f"{float(transaction.risk_score or 0):.0f}/100",
                        },
                        {
                            "label": "Score IA",
                            "value": f"{float(transaction.anomaly_score or 0):.2f}",
                        },
                        {
                            "label": "Statut",
                            "value": STATUS_FR.get(transaction.status, transaction.status),
                        },
                    ]
                )

                st.markdown(
                    f"**Décision :** {DECISION_FR.get(transaction.decision, transaction.decision)} · "
                    f"**Canal :** {CHANNEL_FR.get(transaction.channel, transaction.channel)} · "
                    f"**Lieu :** {html.escape(transaction.city)}, {html.escape(transaction.country)}"
                )

                factors = meta.get("facteurs") or []
                if factors:
                    factors_frame = pd.DataFrame(
                        [
                            {
                                "Facteur": factor.get("label", ""),
                                "Détail": factor.get("detail", ""),
                                "Points": factor.get("weight", 0),
                            }
                            for factor in factors
                        ]
                    )
                    st.dataframe(
                        factors_frame,
                        hide_index=True,
                        use_container_width=True,
                        column_config={
                            "Points": st.column_config.NumberColumn(format="%+.1f"),
                        },
                    )
                if meta.get("explication"):
                    st.caption(meta["explication"])
                if meta.get("fraude_simulee"):
                    st.warning(
                        "Cette opération fait partie d'une fraude **simulée** du jeu de démonstration "
                        f"(scénario : {meta['fraude_simulee']})."
                    )


# ---------------------------------------------------------------------------
# Page : paiement sécurisé
# ---------------------------------------------------------------------------
def _reset_payment_state() -> None:
    for key in ("payment_quote", "payment_meta", "payment_feedback", "last_payment"):
        st.session_state.pop(key, None)


def _payload_from_scenario(payload: dict, context: dict) -> dict:
    """Transforme un scénario de démonstration en requête de paiement."""
    data = dict(payload)
    use_known = bool(data.pop("use_known_device", False))
    declared_label = data.pop("device_label", None)

    known_devices = context.get("devices") or []
    if use_known and known_devices:
        fingerprint = known_devices[0]["fingerprint"]
        device_label = known_devices[0]["label"]
    else:
        fingerprint = f"streamlit-{secrets.token_hex(8)}"
        device_label = declared_label or "Appareil inconnu"

    city = data.get("city") or "Paris"
    return {
        "merchant": data["merchant"],
        "amount": data["amount"],
        "category": data.get("category", "Autres"),
        "city": city,
        "country": data.get("country")
        or (context.get("city_countries") or {}).get(city)
        or "France",
        "channel": data.get("channel", "en_ligne"),
        "device_fingerprint": fingerprint,
        "device_label": device_label,
        "simulated_hour": data.get("simulated_hour"),
        "simulated_minute": data.get("simulated_minute"),
    }


def _run_scenario(db, user: User, scenario: dict) -> None:
    """Exécute un scénario (paiement préparatoire éventuel) puis l'analyse."""
    context = payment_flow.payment_context(db, user)
    notice = None

    prepayment = scenario.get("pre_payment")
    if prepayment:
        prep_payload = _payload_from_scenario(prepayment, context)
        prep_quote = payment_flow.quote(db, user, prep_payload, ip_address="streamlit")
        if prep_quote["challenge_id"] and prep_quote["simulation_code"]:
            challenge = payment_flow.load_challenge(db, user, prep_quote["challenge_id"])
            result = payment_flow.confirm(
                db,
                user,
                challenge,
                prep_quote["simulation_code"],
                ip_address="streamlit",
            )
            if result["status"] == "completed":
                notice = (
                    f"Paiement local de préparation exécuté : {_euro(prep_payload['amount'])} "
                    f"à {html.escape(prep_payload['city'])}."
                )
            else:
                notice = f"Paiement de préparation non exécuté : {result['message']}"
        else:
            notice = "Le paiement de préparation a été bloqué par le moteur de risque."

    payload = _payload_from_scenario(scenario["payload"], context)
    result = payment_flow.quote(db, user, payload, ip_address="streamlit")
    st.session_state["payment_quote"] = result
    st.session_state["payment_meta"] = {
        "payload": payload,
        "amount": payload["amount"],
        "merchant": payload["merchant"],
        "city": payload["city"],
        "notice": notice,
    }
    st.session_state["payment_feedback"] = None
    st.session_state.pop("last_payment", None)
    st.rerun()


def _page_payment(db, user: User) -> None:
    _hero(
        "Paiement sécurisé",
        "Payer avec une validation dynamique",
        "Chaque opération est notée de 0 à 100 avant validation. Un code à usage unique de "
        "60 secondes est exigé ; un code PIN s'y ajoute lorsque le risque est élevé.",
    )

    column1, column2, column3 = st.columns(3)
    with column1:
        st.markdown(
            '<div class="nx-card"><strong>1 · Analyse</strong><div class="nx-muted">'
            "Montant, heure, lieu, appareil et rythme sont comparés à vos habitudes.</div></div>",
            unsafe_allow_html=True,
        )
    with column2:
        st.markdown(
            '<div class="nx-card"><strong>2 · Code à usage unique</strong><div class="nx-muted">'
            "6 chiffres, valables 60 s, usage unique, vérifiés côté serveur (3 tentatives).</div></div>",
            unsafe_allow_html=True,
        )
    with column3:
        st.markdown(
            '<div class="nx-card"><strong>3 · Décision</strong><div class="nx-muted">'
            "Accepter, demander le PIN ou bloquer — avec des facteurs explicables.</div></div>",
            unsafe_allow_html=True,
        )

    # --- Scénarios de démonstration --------------------------------------
    st.markdown("### Scénarios de démonstration")
    st.caption(
        "Ce sont des formulaires préremplis : c'est le moteur de risque qui décide. "
        "L'heure est simulée pour rendre les résultats reproductibles."
    )

    scenarios = payment_flow.demo_scenarios(user)
    columns = st.columns(2)
    for index, scenario in enumerate(scenarios):
        expected_label, expected_css = DECISION_STYLE[scenario["expected"]]
        with columns[index % 2]:
            with st.container(border=True):
                st.markdown(
                    f'<strong>{html.escape(scenario["title"])}</strong> '
                    f'{_badge(expected_label, expected_css)}',
                    unsafe_allow_html=True,
                )
                st.caption(scenario["description"])
                if st.button(
                    "Lancer ce scénario",
                    key=f"scenario_{scenario['code']}",
                    use_container_width=True,
                ):
                    _run_scenario(db, user, scenario)

    # --- Formulaire libre --------------------------------------------------
    st.markdown("### Paiement libre")
    context = payment_flow.payment_context(db, user)
    cities = context.get("cities") or ["Paris"]
    default_city = user.city if user.city in cities else "Paris"
    city_index = cities.index(default_city)

    device_labels = ["🆕 Nouvel appareil (non vérifié)"] + [
        f"{device['label']} ({device['platform']})" for device in context.get("devices", [])
    ]

    with st.form("payment_form"):
        form_col1, form_col2 = st.columns(2)
        with form_col1:
            merchant = st.text_input("Marchand", value="Café des Arts", max_chars=120)
            category = st.selectbox("Catégorie", context.get("categories") or ["Autres"])
            channel = st.selectbox(
                "Canal", list(CHANNEL_FR), format_func=lambda value: CHANNEL_FR[value]
            )
            hour_label = st.selectbox(
                "Heure de l'opération",
                ["Heure actuelle"] + [f"{hour:02d} h" for hour in range(24)],
                help="Une heure simulée permet de tester un paiement nocturne sans attendre.",
            )
        with form_col2:
            amount = st.number_input(
                "Montant (€)",
                min_value=0.01,
                max_value=float(settings.payment_max_amount),
                value=12.50,
                step=1.0,
                format="%.2f",
            )
            city = st.selectbox("Ville", cities, index=city_index)
            device_label = st.selectbox("Appareil", device_labels)
            st.caption(
                f"Solde disponible : **{_euro(context.get('balance'))}** · "
                f"Téléphone : {context.get('masked_phone') or '—'}"
            )
        submitted = st.form_submit_button(
            "Analyser le paiement", type="primary", use_container_width=True
        )

    if submitted:
        if len(merchant.strip()) < 2:
            st.error("Le nom du marchand doit contenir au moins 2 caractères.")
        else:
            device_index = device_labels.index(device_label)
            if device_index == 0:
                fingerprint = f"streamlit-{secrets.token_hex(8)}"
                label = "Navigateur Streamlit inconnu"
            else:
                device = context["devices"][device_index - 1]
                fingerprint = device["fingerprint"]
                label = device["label"]

            payload = {
                "merchant": merchant.strip(),
                "amount": float(amount),
                "category": category,
                "city": city,
                "country": (context.get("city_countries") or {}).get(city, "France"),
                "channel": channel,
                "device_fingerprint": fingerprint,
                "device_label": label,
                "simulated_hour": None if hour_label == "Heure actuelle" else int(hour_label[:2]),
                "simulated_minute": None,
            }
            result = payment_flow.quote(db, user, payload, ip_address="streamlit")
            st.session_state["payment_quote"] = result
            st.session_state["payment_meta"] = {
                "payload": payload,
                "amount": payload["amount"],
                "merchant": payload["merchant"],
                "city": payload["city"],
                "notice": None,
            }
            st.session_state["payment_feedback"] = None
            st.session_state.pop("last_payment", None)
            st.rerun()

    # --- Résultat de la dernière validation --------------------------------
    last_payment = st.session_state.get("last_payment")
    if last_payment:
        st.markdown("---")
        st.success(last_payment["message"])
        _kpis(
            [
                {"label": "Nouveau solde", "value": _euro(last_payment.get("new_balance"))},
                {
                    "label": "Score de risque",
                    "value": f"{float(last_payment.get('risk_score', 0)):.0f}/100",
                },
                {
                    "label": "Score IA",
                    "value": f"{float(last_payment.get('anomaly_score', 0)):.2f}",
                },
            ]
        )
        if last_payment.get("alert_id"):
            st.warning(
                "Cette opération reste atypique pour votre profil : une alerte de sécurité a été créée."
            )
        if st.button("Nouveau paiement", use_container_width=False):
            _reset_payment_state()
            st.rerun()

    # --- Analyse en cours --------------------------------------------------
    quote = st.session_state.get("payment_quote")
    if not quote:
        return

    meta = st.session_state.get("payment_meta") or {}
    st.markdown("---")
    st.markdown("### Résultat de l'analyse")

    if meta.get("notice"):
        st.info(meta["notice"])

    decision = quote["decision"]
    decision_label, decision_css = DECISION_STYLE[decision]
    st.markdown(
        f'{_badge(decision_label, decision_css)} '
        f'<span class="nx-muted">Score de risque {float(quote["risk_score"]):.0f}/100 · '
        f'Score IA {float(quote["anomaly_score"]):.2f}</span>',
        unsafe_allow_html=True,
    )
    st.progress(
        min(1.0, max(0.0, float(quote["risk_score"]) / 100.0)),
        text=f"Risque : {float(quote['risk_score']):.0f}/100",
    )
    st.markdown(f"**{html.escape(quote['summary'])}**")

    if meta.get("merchant"):
        st.caption(
            f"Opération : {_euro(meta.get('amount'))} vers « {html.escape(meta.get('merchant', ''))} » "
            f"à {html.escape(meta.get('city', ''))}."
        )

    factors = quote.get("factors") or []
    if factors:
        with st.expander("Voir les facteurs de risque", expanded=True):
            st.dataframe(
                pd.DataFrame(
                    [
                        {
                            "Facteur": factor.get("label", ""),
                            "Détail": factor.get("detail", ""),
                            "Points": factor.get("weight", 0),
                            "Gravité": factor.get("severity", ""),
                        }
                        for factor in factors
                    ]
                ),
                hide_index=True,
                use_container_width=True,
                column_config={"Points": st.column_config.NumberColumn(format="%+.1f")},
            )

    feedback = st.session_state.get("payment_feedback")
    if feedback:
        if feedback.get("kind") == "error":
            st.error(feedback.get("message", ""))
        else:
            st.info(feedback.get("message", ""))

    if decision == "block":
        st.error(
            "Paiement bloqué : aucune validation n'est possible. Une alerte de sécurité a été "
            "enregistrée dans votre centre de sécurité."
        )
        if st.button("Nouvelle analyse", use_container_width=False):
            _reset_payment_state()
            st.rerun()
        return

    _render_otp_panel(db, user, quote, meta)


def _render_otp_panel(db, user: User, quote: dict, meta: dict) -> None:
    """Affiche le code, le compte à rebours et le formulaire de validation."""
    challenge = payment_flow.load_challenge(db, user, quote["challenge_id"])
    if challenge is None:
        st.error("Demande de paiement introuvable. Relancez l'analyse.")
        if st.button("Nouvelle analyse", use_container_width=False):
            _reset_payment_state()
            st.rerun()
        return

    if challenge.consumed_at is not None:
        st.info("Ce défi a déjà été utilisé ou annulé. Relancez l'analyse pour un nouveau code.")
        if st.button("Nouvelle analyse", use_container_width=False):
            _reset_payment_state()
            st.rerun()
        return

    remaining = seconds_remaining(challenge)
    if remaining <= 0:
        st.error("Le code a expiré. Vous pouvez en demander un nouveau.")
        if st.button("Obtenir un nouveau code", use_container_width=False):
            _new_code(db, user, meta)
        return

    st.markdown("#### Validation par code à usage unique")

    if quote.get("simulation_code"):
        st.markdown(
            f'<div class="nx-card">Code de démonstration '
            f'<span class="nx-code">{html.escape(str(quote["simulation_code"]))}</span>'
            f'<div class="nx-muted">En production, ce code serait envoyé par SMS au '
            f'{html.escape(quote.get("masked_phone") or "")}. '
            f'Valable {settings.otp_ttl_seconds} secondes, à usage unique.</div></div>',
            unsafe_allow_html=True,
        )
    else:
        st.info(f"Un code a été envoyé au {quote.get('masked_phone') or 'numéro enregistré'}.")

    _otp_countdown(challenge.expires_at, settings.otp_ttl_seconds)
    st.caption(f"Tentatives restantes : {attempts_left(challenge)} / {settings.otp_max_attempts}.")

    if quote.get("require_pin"):
        st.warning(
            "Vérification renforcée : le code à usage unique **et** votre code PIN sont exigés."
        )

    with st.form("otp_form"):
        code = st.text_input("Code à 6 chiffres", max_chars=6, placeholder="••••••")
        pin = ""
        if quote.get("require_pin"):
            pin = st.text_input(
                "Code PIN",
                type="password",
                max_chars=6,
                help=f"Code PIN de démonstration : {DEMO_PIN}",
            )
        validated = st.form_submit_button(
            "Valider le paiement", type="primary", use_container_width=True
        )

    action_col1, action_col2 = st.columns(2)
    if action_col1.button("Obtenir un nouveau code", use_container_width=True):
        _new_code(db, user, meta)
    if action_col2.button("Annuler ce paiement", use_container_width=True):
        payment_flow.cancel(db, challenge, user.id, ip_address="streamlit")
        st.session_state["payment_feedback"] = {
            "kind": "info",
            "message": "Paiement annulé. Aucun montant n'a été débité.",
        }
        st.session_state.pop("payment_quote", None)
        st.session_state.pop("payment_meta", None)
        st.rerun()

    if validated:
        if not (code.isdigit() and len(code) == 6):
            st.error("Saisissez les 6 chiffres du code à usage unique.")
            return
        if quote.get("require_pin") and not pin:
            st.error("Le code PIN est requis pour cette opération sensible.")
            return

        with st.spinner("Vérification côté serveur…"):
            result = payment_flow.confirm(
                db,
                user,
                challenge,
                code,
                pin or None,
                ip_address="streamlit",
            )

        if result["status"] == "error":
            st.session_state["payment_feedback"] = {
                "kind": "error",
                "message": result["message"],
            }
            st.rerun()

        st.session_state["last_payment"] = result
        st.session_state.pop("payment_quote", None)
        st.session_state.pop("payment_meta", None)
        st.session_state["payment_feedback"] = None
        st.rerun()


def _new_code(db, user: User, meta: dict) -> None:
    """Relance l'analyse pour obtenir un nouveau défi (même opération)."""
    payload = meta.get("payload")
    if not payload:
        _reset_payment_state()
        st.rerun()
    result = payment_flow.quote(db, user, payload, ip_address="streamlit")
    st.session_state["payment_quote"] = result
    st.session_state["payment_feedback"] = None
    st.rerun()


@st.fragment(run_every=1.0)
def _otp_countdown(expires_at: datetime, ttl_seconds: int) -> None:
    """Compte à rebours rafraîchi chaque seconde (sans recharger la page)."""
    remaining = max(0, int((expires_at - now_utc()).total_seconds()))
    ratio = min(1.0, max(0.0, remaining / max(ttl_seconds, 1)))
    st.progress(ratio, text=f"Temps restant : {remaining} s")
    if remaining == 0:
        st.error("Code expiré. Demandez un nouveau code pour poursuivre.")


# ---------------------------------------------------------------------------
# Page : sécurité & IA
# ---------------------------------------------------------------------------
def _page_security(db, user: User) -> None:
    profile = get_profile(db, user)
    unread = unread_alert_count(db, user.id)
    _hero(
        "Sécurité & intelligence artificielle",
        "Votre profil, vos anomalies, vos alertes",
        "Le modèle Isolation Forest compare chaque opération à vos habitudes. "
        f"{unread} alerte(s) non lue(s) pour le moment.",
    )

    stats = profile.get("amount_stats") or {}
    tab_profile, tab_anomalies, tab_alerts, tab_devices, tab_audit = st.tabs(
        ["🧠 Profil comportemental", "🔎 Anomalies", "🔔 Alertes", "📱 Appareils", "📜 Journal"]
    )

    # --- Profil -----------------------------------------------------------
    with tab_profile:
        _kpis(
            [
                {"label": "Panier moyen", "value": _euro(stats.get("mean"))},
                {"label": "Médiane", "value": _euro(stats.get("median"))},
                {
                    "label": "Dépense mensuelle moy.",
                    "value": _euro(profile.get("average_monthly_spending")),
                },
                {"label": "Opérations analysées", "value": str(profile.get("count", 0))},
            ]
        )

        st.caption(
            f"Fourchette habituelle : {_euro(stats.get('p10'))} à {_euro(stats.get('p90'))} "
            f"· heure favorite : {(profile.get('typical_hours') or ['—'])[0]} h "
            f"· nuit : {_pct(profile.get('night_ratio'))} "
            f"· en ligne : {_pct(profile.get('online_ratio'))}."
        )

        col_left, col_right = st.columns(2)
        with col_left:
            st.markdown("##### Répartition horaire")
            hours_frame = pd.DataFrame(
                {
                    "Heure": [f"{hour:02d}h" for hour in range(24)],
                    "Part (%)": [
                        round(float(value) * 100, 2) for value in profile.get("hourly_hist", [])
                    ],
                }
            )
            st.bar_chart(hours_frame, x="Heure", y="Part (%)", height=220)
        with col_right:
            st.markdown("##### Répartition par jour")
            weekdays_frame = pd.DataFrame(
                {
                    "Jour": WEEKDAYS_FR,
                    "Part (%)": [
                        round(float(value) * 100, 2) for value in profile.get("weekday_hist", [])
                    ],
                }
            )
            st.bar_chart(weekdays_frame, x="Jour", y="Part (%)", height=220)

        st.markdown("##### Observations")
        for insight in profile.get("insights", []):
            st.markdown(f"- {html.escape(insight)}")

        col_left, col_right = st.columns(2)
        with col_left:
            st.markdown("##### Principales catégories")
            top_categories = profile.get("top_categories") or []
            if top_categories:
                st.dataframe(
                    pd.DataFrame(
                        [
                            {
                                "Catégorie": row["category"],
                                "Total": _euro(row["total"]),
                                "Opérations": row["count"],
                                "Part": _pct(row["share"]),
                            }
                            for row in top_categories
                        ]
                    ),
                    hide_index=True,
                    use_container_width=True,
                )
        with col_right:
            st.markdown("##### Abonnements récurrents")
            subscriptions = profile.get("subscriptions") or []
            if subscriptions:
                st.dataframe(
                    pd.DataFrame(
                        [
                            {
                                "Marchand": row["merchant"],
                                "Montant": _euro(row["amount"]),
                                "Occurrences": row["occurrences"],
                            }
                            for row in subscriptions
                        ]
                    ),
                    hide_index=True,
                    use_container_width=True,
                )
            else:
                st.caption("Aucun abonnement détecté.")

        st.markdown("##### Recommandations personnalisées")
        recommendations = build_recommendations(db, user, profile)
        for recommendation in recommendations:
            kind_label, kind_css = RECOMMENDATION_STYLE.get(
                recommendation["kind"], (recommendation["kind"], "nx-neutral")
            )
            impact = (
                f" · impact estimé <strong>{_euro(recommendation['impact_eur'])} / mois</strong>"
                if recommendation.get("impact_eur")
                else ""
            )
            st.markdown(
                f'<div class="nx-card">{_badge(kind_label, kind_css)} '
                f'<strong>{html.escape(recommendation["title"])}</strong>'
                f'<div style="font-size:.88rem;color:#c7d3e4;margin-top:.3rem;">'
                f"{html.escape(recommendation['detail'])}</div>"
                f'<div class="nx-muted" style="margin-top:.25rem;">Priorité {recommendation["priority"]}'
                f"{impact} · {html.escape(recommendation.get('action', ''))}</div></div>",
                unsafe_allow_html=True,
            )

    # --- Anomalies --------------------------------------------------------
    with tab_anomalies:
        rows = flagged_transactions(db, user, limit=10)
        st.caption(
            "Opérations les plus atypiques selon l'Isolation Forest (revenue et charges régulières exclus). "
            "Un score proche de 1 signale un comportement très éloigné de vos habitudes."
        )
        if not rows:
            st.info("Aucune anomalie détectée dans l'historique.")
        else:
            ordered = list(
                db.scalars(
                    select(Transaction)
                    .where(Transaction.user_id == user.id)
                    .order_by(Transaction.ts.asc())
                ).all()
            )
            previous_by_id: dict[int, Transaction | None] = {}
            previous = None
            for transaction in ordered:
                previous_by_id[transaction.id] = previous
                previous = transaction

            for transaction in rows:
                context = context_from_transaction(transaction, previous_by_id.get(transaction.id))
                features = build_features(context, profile)
                reasons = explain(features, context, profile)
                st.markdown(
                    f'<div class="nx-card">{_badge("Anomalie IA", "nx-block")} '
                    f'<strong>{html.escape(transaction.merchant)}</strong> '
                    f'<span class="nx-muted">{_date(transaction.ts)} · '
                    f"{_euro(abs(transaction.amount))} · {html.escape(transaction.city)}</span>"
                    f'<div style="font-size:.86rem;color:#c7d3e4;margin-top:.35rem;">'
                    f"{html.escape(' '.join(reasons))}</div>"
                    f'<div class="nx-muted" style="margin-top:.3rem;">'
                    f"Score IA {float(transaction.anomaly_score or 0):.2f} · "
                    f"Score risque {float(transaction.risk_score or 0):.0f}/100 · "
                    f"Statut : {STATUS_FR.get(transaction.status, transaction.status)}</div></div>",
                    unsafe_allow_html=True,
                )

    # --- Alertes ----------------------------------------------------------
    with tab_alerts:
        alerts = list(
            db.scalars(
                select(Alert).where(Alert.user_id == user.id).order_by(Alert.created_at.desc())
            ).all()
        )
        unread_alerts = sum(1 for alert in alerts if alert.read_at is None)
        col1, col2 = st.columns([3, 1])
        col1.markdown(f"**{len(alerts)} alerte(s)** — {unread_alerts} non lue(s)")
        if col2.button(
            "Tout marquer comme lu", disabled=unread_alerts == 0, use_container_width=True
        ):
            for alert in alerts:
                if alert.read_at is None:
                    alert.read_at = now_utc()
            db.commit()
            st.rerun()

        if not alerts:
            st.info("Aucune alerte.")
        for alert in alerts:
            severity_label, severity_css = ALERT_SEVERITY_STYLE.get(
                alert.severity, (alert.severity, "nx-neutral")
            )
            marker = (
                ' <span class="nx-pill nx-info">Non lue</span>'
                if alert.read_at is None
                else ' <span class="nx-pill nx-neutral">Lue</span>'
            )
            st.markdown(
                f'<div class="nx-card">{_badge(severity_label, severity_css)} '
                f'<strong>{html.escape(alert.title)}</strong>{marker}'
                f'<div class="nx-muted">{_date(alert.created_at)} · {html.escape(alert.kind)}</div>'
                f'<div style="font-size:.87rem;color:#c7d3e4;margin-top:.3rem;">'
                f"{html.escape(alert.message)}</div></div>",
                unsafe_allow_html=True,
            )
            if alert.read_at is None and st.button(
                "Marquer comme lue", key=f"alert_read_{alert.id}"
            ):
                mark_alert_read(db, user.id, alert.id)
                st.rerun()

    # --- Appareils --------------------------------------------------------
    with tab_devices:
        devices = list(
            db.scalars(
                select(Device).where(Device.user_id == user.id).order_by(Device.last_seen.desc())
            ).all()
        )
        st.caption(
            "Un appareil non vérifié ne bloque pas le paiement, mais déclenche une vérification renforcée."
        )
        if devices:
            st.dataframe(
                pd.DataFrame(
                    [
                        {
                            "Appareil": device.label,
                            "Plateforme": device.platform,
                            "Vérifié": "Oui" if device.is_trusted else "Non",
                            "Dernière utilisation": _date(device.last_seen),
                        }
                        for device in devices
                    ]
                ),
                hide_index=True,
                use_container_width=True,
            )

            selected = st.selectbox(
                "Gérer un appareil",
                devices,
                format_func=lambda device: f"{device.label} — {device.platform}",
            )
            col1, col2 = st.columns(2)
            if col1.button(
                "Marquer comme vérifié",
                disabled=selected.is_trusted,
                use_container_width=True,
            ):
                selected.is_trusted = True
                db.commit()
                log_action(
                    db,
                    "appareil_verifie",
                    user.id,
                    "ok",
                    f"Appareil « {selected.label} » vérifié (Streamlit).",
                    "streamlit",
                )
                st.rerun()

            confirm = st.checkbox("Je confirme la révocation de cet appareil")
            if col2.button(
                "Révoquer l'appareil",
                disabled=not confirm,
                use_container_width=True,
            ):
                label = selected.label
                db.delete(selected)
                db.commit()
                log_action(
                    db,
                    "appareil_revoque",
                    user.id,
                    "ok",
                    f"Appareil « {label} » révoqué (Streamlit).",
                    "streamlit",
                )
                st.rerun()
        else:
            st.info("Aucun appareil enregistré.")

    # --- Journal d'audit --------------------------------------------------
    with tab_audit:
        entries = list(
            db.scalars(
                select(AuditLog)
                .where(AuditLog.user_id == user.id)
                .order_by(AuditLog.created_at.desc())
                .limit(40)
            ).all()
        )
        st.caption("Journal d'audit : connexions, analyses, validations, blocages et actions de sécurité.")
        if entries:
            st.dataframe(
                pd.DataFrame(
                    [
                        {
                            "Date": _date(entry.created_at),
                            "Action": entry.action,
                            "Statut": entry.status,
                            "Détail": entry.detail,
                        }
                        for entry in entries
                    ]
                ),
                hide_index=True,
                use_container_width=True,
            )
        else:
            st.info("Aucune entrée d'audit.")


# ---------------------------------------------------------------------------
# Page : modèle & données
# ---------------------------------------------------------------------------
def _page_model(db, user: User) -> None:
    card = model_card(db)
    metrics = card.get("metrics") or {}
    _hero(
        "Transparence",
        "Modèle & données",
        "Isolation Forest, variables comportementales, métriques de détection et cycle de vie du modèle.",
    )

    model_feedback = st.session_state.pop("model_feedback", None)
    if model_feedback:
        if model_feedback.get("kind") == "success":
            st.success(model_feedback.get("message", ""))
        else:
            st.error(model_feedback.get("message", ""))

    if not card.get("available"):
        st.warning(
            "Le modèle n'est pas encore entraîné. Utilisez le bouton de réentraînement ci-dessous."
        )

    _kpis(
        [
            {"label": "ROC AUC", "value": f"{float(metrics.get('roc_auc', 0)):.3f}" if metrics else "—"},
            {"label": "Précision", "value": _pct(metrics.get("precision")) if metrics else "—"},
            {"label": "Rappel", "value": _pct(metrics.get("recall")) if metrics else "—"},
            {"label": "F1", "value": f"{float(metrics.get('f1', 0)):.3f}" if metrics else "—"},
        ]
    )
    st.caption(
        f"Algorithme : {card.get('algorithm')} · {card.get('n_samples', 0)} exemples · "
        f"{card.get('n_features', 0)} variables · contamination {card.get('contamination')}. "
        + (
            f"Seuil opérationnel : {metrics.get('best_threshold')} "
            f"(F1 {metrics.get('f1')}, rappel {metrics.get('recall')})."
            if metrics
            else ""
        )
    )

    with st.expander("Variables utilisées par le modèle", expanded=False):
        for feature in card.get("features", []):
            st.markdown(f"- {html.escape(feature)}")
        st.caption(
            "Les encaissements (salaire, virements reçus), les opérations bloquées et les fraudes "
            "simulées sont exclus de l'apprentissage : le modèle décrit le comportement de dépense réel."
        )

    st.markdown("### Évaluation du moteur de décision complet")
    st.caption(
        "Le score d'anomalie seul ne suffit pas : cette évaluation rejoue le moteur complet "
        "(règles + IA) sur toutes les fraudes simulées et un échantillon de paiements légitimes."
    )
    if st.button("Lancer l'évaluation (environ 30 secondes)"):
        with st.spinner("Rejeu du moteur sur l'historique…"):
            st.session_state["decision_eval"] = evaluate_decisions(db)

    decision_eval = st.session_state.get("decision_eval")
    if decision_eval:
        _kpis(
            [
                {
                    "label": "Taux de détection",
                    "value": _pct(decision_eval.get("taux_detection")),
                    "tone": "up",
                },
                {
                    "label": "Taux de friction",
                    "value": _pct(decision_eval.get("taux_friction")),
                    "tone": "down",
                },
                {"label": "Fraudes évaluées", "value": str(decision_eval.get("n_fraudes", 0))},
                {
                    "label": "Paiements légitimes",
                    "value": str(decision_eval.get("n_legitimes", 0)),
                },
            ]
        )
        st.caption(
            f"Fraudes bloquées : {decision_eval.get('fraudes_bloquees', 0)} · "
            f"escaladées : {decision_eval.get('fraudes_escaladees', 0)} · "
            f"acceptées : {decision_eval.get('fraudes_acceptees', 0)}. "
            f"Légitimes acceptés : {decision_eval.get('legitimes_acceptees', 0)} · "
            f"escaladés : {decision_eval.get('legitimes_escaladees', 0)} · "
            f"bloqués : {decision_eval.get('legitimes_bloques', 0)}."
        )
        st.caption(
            f"Score moyen des fraudes : {decision_eval.get('score_moyen_fraude', 0)}/100 · "
            f"score moyen des paiements légitimes : {decision_eval.get('score_moyen_legitime', 0)}/100."
        )

    st.markdown("### Cycle de vie du modèle")
    st.caption(
        "En production, l'entraînement serait planifié (par exemple chaque nuit) sur des données "
        "anonymisées. Ici, il est déclenché à la demande pour la démonstration."
    )
    if st.button("Réentraîner l'Isolation Forest"):
        with st.spinner("Entraînement et re-scoring de l'historique…"):
            report = train_detector(db)
        log_action(
            db,
            "reentrainement_modele",
            user.id,
            "ok" if report.get("status") == "ok" else "echec",
            report.get("message", ""),
            "streamlit",
        )
        st.session_state.pop("decision_eval", None)
        st.session_state["model_feedback"] = {
            "kind": "success" if report.get("status") == "ok" else "error",
            "message": report.get("message", "Réentraînement terminé."),
        }
        st.rerun()

    st.markdown("### Jeu de données de démonstration")
    _kpis(
        [
            {"label": "Utilisateurs", "value": str(int(db.scalar(select(func.count(User.id))) or 0))},
            {
                "label": "Transactions",
                "value": str(int(db.scalar(select(func.count(Transaction.id))) or 0)),
            },
            {
                "label": "Fraudes simulées",
                "value": str(
                    int(
                        db.scalar(
                            select(func.count(Transaction.id)).where(Transaction.is_fraud.is_(True))
                        )
                        or 0
                    )
                ),
            },
            {
                "label": "Paiements bloqués",
                "value": str(
                    int(
                        db.scalar(
                            select(func.count(Transaction.id)).where(
                                Transaction.status == "blocked"
                            )
                        )
                        or 0
                    )
                ),
            },
        ]
    )

    st.caption(
        "Base SQLite créée automatiquement au premier lancement. Sur Streamlit Cloud, le disque "
        "est éphémère : les données sont régénérées à chaque redémarrage. En local, "
        "`python -m app.seed --force` régénère le jeu de démonstration."
    )

    with st.expander("Comptes de démonstration"):
        st.markdown(
            f"Mot de passe commun : `{DEMO_PASSWORD}` — code PIN : `{DEMO_PIN}`\n\n"
            + "\n".join(f"- `{mail}` — {name}" for mail, name, _ in DEMO_ACCOUNTS)
        )


# ---------------------------------------------------------------------------
# Point d'entrée
# ---------------------------------------------------------------------------
def main() -> None:
    st.markdown(STYLE, unsafe_allow_html=True)

    with st.spinner("Préparation de l'environnement de démonstration…"):
        _bootstrap()

    with _db_session() as db:
        user = _current_user(db)
        if user is None:
            _render_login(db)
            return
        _render_shell(db, user)


if __name__ == "__main__":
    main()
