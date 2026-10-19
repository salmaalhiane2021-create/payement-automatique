"""
Tests internationaux : authentification partagée, formats, devises, i18n,
et signal géographique fondé sur l'historique (jamais sur la nationalité).

Points vérifiés
---------------
1. **Auth partagée** : création de compte internationale (téléphone E.164,
   pays ISO, locale, fuseau, devise), anti-énumération, verrouillage,
   compte désactivé, mise à jour des préférences régionales.
2. **Formats localisés** : montants, pourcentages, dates et fuseaux via Babel
   et zoneinfo — aucun format national codé en dur.
3. **Devises** : conversion désactivée par défaut, activée uniquement avec une
   source configurée ; aucun taux croisé inventé.
4. **i18n** : catalogues français/anglais complets, repli sans exception.
5. **Géographie** : le pays n'est jamais un motif en soi ; le signal
   « zone inhabituelle » est comparé à l'historique du compte.

Exécution :
    .venv\\Scripts\\python -m pytest tests/test_international.py -q
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.config import settings
from app.database import Base, now_utc
from app.i18n import catalog_keys, normalize_language, t
from app.ml.features import build_features
from app.models import Account, Transaction, User
from app.services import fx, user_prefs
from app.services.auth import authenticate, create_user_account, update_user_profile
from app.services.risk_engine import assess_payment

TZ = ZoneInfo(settings.app_timezone)
PARIS = (48.8566, 2.3522)
LAGOS = (6.5244, 3.3792)
PASSWORD = "Test1234!"


# ---------------------------------------------------------------------------
# Fixtures et helpers
# ---------------------------------------------------------------------------
@pytest.fixture()
def db():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
        future=True,
    )
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine, expire_on_commit=False, future=True)()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


@pytest.fixture()
def rules_only(monkeypatch):
    def fake_score_context(context: dict, profile: dict):
        return 0.0, build_features(context, profile), []

    monkeypatch.setattr("app.services.risk_engine.score_context", fake_score_context)


def register_international(db, email: str = "alex@example.com") -> User:
    result = create_user_account(
        db,
        full_name="Alex Johnson",
        email=email,
        password=PASSWORD,
        pin="9876",
        phone="+1 202 555 0147",
        city="New York",
        country="",
        country_code="US",
        locale="en-US",
        timezone="America/New_York",
        currency="USD",
    )
    assert result.ok, result.message
    assert result.user is not None
    return result.user


def add_history(
    db,
    user: User,
    account: Account,
    city: str,
    country: str,
    latitude: float,
    longitude: float,
    days: int = 50,
) -> None:
    today = now_utc().date()
    for index in range(days):
        day = today - timedelta(days=days - index)
        aware = datetime(day.year, day.month, day.day, 13, tzinfo=TZ)
        db.add(
            Transaction(
                user_id=user.id,
                account_id=account.id,
                ts=aware.astimezone(timezone.utc).replace(tzinfo=None),
                amount=-round(9 + (index * 5) % 30, 2),
                label="Achat local",
                merchant=["Supermarché Colibri", "Boulangerie Maison Blanc"][index % 2],
                category="Alimentation",
                channel="carte",
                city=city,
                country=country,
                latitude=latitude,
                longitude=longitude,
                device_fingerprint="fp-appareil-de-confiance",
                status="completed",
            )
        )
    db.commit()


def make_user_with_history(db, email: str, city: str, country: str, coords) -> tuple[User, dict]:
    """Client dont l'historique est localisé dans `city` (pays donné)."""
    result = create_user_account(
        db,
        full_name="Client Test",
        email=email,
        password=PASSWORD,
        pin="4321",
        phone="0611223344",
        city=city,
        country=country,
        country_code="FR",
    )
    assert result.ok
    user = result.user
    account = db.scalars(
        select(Account).where(Account.user_id == user.id, Account.kind == "courant")
    ).first()
    assert account is not None
    add_history(db, user, account, city, country, coords[0], coords[1])

    from app.services.behavior import build_profile

    return user, build_profile(db, user)


# ---------------------------------------------------------------------------
# 1. Authentification partagée
# ---------------------------------------------------------------------------
class TestSharedAuthentication:
    def test_international_registration_normalizes_everything(self, db):
        user = register_international(db)
        assert user.phone == "+12025550147"          # E.164
        assert user.country_code == "US"             # ISO 3166-1 alpha-2
        assert user.locale == "en-US"                # BCP 47
        assert user.timezone == "America/New_York"   # IANA
        assert user.currency == "USD"                # ISO 4217
        assert user.country == "United States"       # libellé localisé
        # Aucune nationalité n'est demandée ni stockée.
        assert not hasattr(user, "nationality")

    def test_duplicate_email_is_rejected_with_conflict_code(self, db):
        register_international(db, "dup@example.com")
        result = create_user_account(
            db,
            full_name="Autre Personne",
            email="DUP@example.com",  # insensible à la casse
            password=PASSWORD,
            pin="1234",
        )
        assert not result.ok
        assert result.error_code == "conflict"

    def test_invalid_phone_currency_timezone_are_rejected(self, db):
        bad_phone = create_user_account(
            db,
            full_name="Test Invalide",
            email="bad-phone@example.com",
            password=PASSWORD,
            pin="1234",
            phone="123",
            country_code="US",
        )
        assert not bad_phone.ok and bad_phone.error_code == "phone"

        bad_currency = create_user_account(
            db,
            full_name="Test Invalide",
            email="bad-currency@example.com",
            password=PASSWORD,
            pin="1234",
            currency="ZZZ",
        )
        assert not bad_currency.ok and bad_currency.error_code == "currency"

        bad_timezone = create_user_account(
            db,
            full_name="Test Invalide",
            email="bad-tz@example.com",
            password=PASSWORD,
            pin="1234",
            timezone="Mars/Olympus",
        )
        assert not bad_timezone.ok and bad_timezone.error_code == "timezone"

    def test_anti_enumeration_message_is_identical(self, db):
        user = register_international(db)
        wrong_password = authenticate(db, user.email, "Mauvais123!")
        unknown_email = authenticate(db, "inconnu@example.com", "Mauvais123!")
        assert wrong_password.status == "invalid"
        assert unknown_email.status == "invalid"
        assert wrong_password.message == unknown_email.message

    def test_lockout_then_refusal_and_reset_after_success(self, db):
        user = register_international(db)
        for _ in range(settings.login_max_attempts):
            result = authenticate(db, user.email, "Mauvais123!")
            assert result.status in {"invalid", "locked"}
        locked = authenticate(db, user.email, PASSWORD)
        assert locked.status == "locked"
        assert "verrouillé" in locked.message
        assert locked.remaining_minutes >= 1

    def test_successful_login_resets_counters(self, db):
        user = register_international(db)
        authenticate(db, user.email, "Mauvais123!")
        db.refresh(user)
        assert user.failed_attempts == 1
        result = authenticate(db, user.email, PASSWORD)
        assert result.status == "ok"
        db.refresh(user)
        assert user.failed_attempts == 0
        assert user.locked_until is None
        assert user.last_login_at is not None

    def test_inactive_account_is_reported(self, db):
        user = register_international(db)
        user.is_active = False
        db.commit()
        result = authenticate(db, user.email, PASSWORD)
        assert result.status == "inactive"

    def test_profile_update_validates_and_persists(self, db):
        user = register_international(db)
        ok, message = update_user_profile(
            db,
            user,
            city="Toronto",
            country_code="CA",
            country="",
            phone="+1 416 555 0123",
            timezone="America/Toronto",
            currency="CAD",
            locale="en-CA",
        )
        assert ok, message
        db.refresh(user)
        assert (user.city, user.country_code, user.timezone, user.currency) == (
            "Toronto",
            "CA",
            "America/Toronto",
            "CAD",
        )
        assert user.phone == "+14165550123"
        assert user.country == "Canada"

        ok, message = update_user_profile(db, user, timezone="Mars/Olympus")
        assert not ok
        assert "Fuseau" in message or "time zone" in message.lower()

    def test_lockout_is_per_account_not_per_country(self, db):
        """Aucune règle ne dépend du pays de résidence ou d'origine."""
        user = register_international(db, "first@example.com")
        other = register_international(db, "second@example.com")
        for _ in range(settings.login_max_attempts):
            authenticate(db, user.email, "Mauvais123!")
        # Verrouiller un compte ne doit pas affecter un autre compte.
        assert authenticate(db, other.email, PASSWORD).status == "ok"
        assert authenticate(db, user.email, PASSWORD).status == "locked"


# ---------------------------------------------------------------------------
# 2. Formats localisés et téléphones
# ---------------------------------------------------------------------------
class TestLocalizedFormats:
    def test_money_format_follows_locale(self):
        french = user_prefs.format_money(1234.5, "EUR", "fr-FR")
        american = user_prefs.format_money(1234.5, "USD", "en-US")
        assert french != american
        assert "€" in french and "$" in american
        # En français, la virgule est le séparateur décimal ; en anglais, le point.
        assert "," in french and "." not in french
        assert "." in american

    def test_percent_format_follows_locale(self):
        assert user_prefs.format_percent(0.222, "fr-FR") == "22,2 %"
        assert user_prefs.format_percent(0.222, "en-US") == "22.2%"

    def test_timezone_conversion_and_formatting(self):
        value = datetime(2026, 1, 15, 12, 0)  # 12:00 UTC naïf
        assert user_prefs.to_timezone(value, "Europe/Paris").hour == 13
        assert user_prefs.to_timezone(value, "America/New_York").hour == 7
        assert user_prefs.format_datetime(value, "Europe/Paris", "fr-FR") != user_prefs.format_datetime(
            value, "America/New_York", "en-US"
        )

    def test_locale_currency_timezone_and_country_validation(self):
        assert user_prefs.normalize_locale("en_US") == "en-US"
        assert user_prefs.normalize_currency("usd") == "USD"
        assert user_prefs.normalize_timezone("America/New_York") == "America/New_York"
        assert user_prefs.normalize_country_code("us") == "US"
        for call in (
            lambda: user_prefs.normalize_locale("zz_ZZ"),
            lambda: user_prefs.normalize_currency("ZZZ"),
            lambda: user_prefs.normalize_timezone("Mars/Olympus"),
            lambda: user_prefs.normalize_country_code("ZZ"),
        ):
            with pytest.raises(ValueError):
                call()
        assert user_prefs.country_name("US", "fr-FR") == "États-Unis"

    def test_phone_normalization_international(self):
        assert user_prefs.normalize_phone("06 12 34 56 78", "FR") == "+33612345678"
        assert user_prefs.normalize_phone("+1 202 555 0147") == "+12025550147"
        assert user_prefs.normalize_phone("") == ""
        assert user_prefs.mask_phone("+12025550147").endswith("47")
        assert user_prefs.mask_phone("").startswith("numéro")
        with pytest.raises(ValueError):
            user_prefs.normalize_phone("123", None)

    def test_reference_lists_cover_countries_and_currencies(self):
        countries = dict(user_prefs.country_options("fr-FR"))
        assert countries.get("FR") == "France"
        assert countries.get("US") == "États-Unis"
        currencies = dict(user_prefs.currency_options("en-US"))
        assert {"EUR", "USD", "GBP"} <= set(currencies)
        assert "Europe/Paris" in user_prefs.timezone_options()
        assert ("fr-FR", "Français (France) (fr-FR)") in user_prefs.locale_options() or any(
            code == "fr-FR" for code, _ in user_prefs.locale_options()
        )


# ---------------------------------------------------------------------------
# 3. Devises : aucune conversion inventée
# ---------------------------------------------------------------------------
class TestCurrencyConversion:
    def test_disabled_without_configured_source(self, monkeypatch):
        monkeypatch.setattr(settings, "fx_rates_json", "")
        monkeypatch.setattr(settings, "fx_rates_source", "")
        assert fx.get_converter() is None
        assert fx.convert(100.0, "EUR", "USD") is None
        # Même devise : aucune conversion nécessaire.
        assert fx.convert(100.0, "EUR", "EUR") == 100.0

    def test_static_source_only_converts_known_rates(self, monkeypatch):
        monkeypatch.setattr(settings, "fx_rates_json", '{"USD": 1.08}')
        monkeypatch.setattr(settings, "fx_rates_source", "source de test")
        converter = fx.get_converter()
        assert converter is not None and converter.name == "source de test"
        assert fx.convert(100.0, "EUR", "USD") == 108.0
        # Taux absent ou devise croisée : aucune invention.
        assert fx.convert(100.0, "EUR", "GBP") is None
        assert fx.convert(100.0, "USD", "EUR") is None

    def test_malformed_configuration_is_ignored(self, monkeypatch):
        monkeypatch.setattr(settings, "fx_rates_json", "pas du json")
        assert fx.get_converter() is None
        monkeypatch.setattr(settings, "fx_rates_json", '{"USD": -3}')
        assert fx.get_converter() is None


# ---------------------------------------------------------------------------
# 4. Internationalisation
# ---------------------------------------------------------------------------
class TestI18n:
    def test_catalogs_have_identical_keys(self):
        assert catalog_keys("fr") == catalog_keys("en")
        assert catalog_keys("fr"), "catalogue français vide"

    def test_translation_and_fallback(self):
        assert t("decision.block", "fr") == "Paiement bloqué"
        assert t("decision.block", "en") == "Payment blocked"
        assert t("decision.block", "en-US") == "Payment blocked"  # locale -> langue
        assert t("cle.absente") == "cle.absente"                  # jamais d'exception
        assert "12.50 €" in t("payment.completed", "fr", amount="12.50 €", merchant="Café")

    def test_language_normalization(self):
        assert normalize_language("fr-FR") == "fr"
        assert normalize_language("EN_us") == "en"
        assert normalize_language(None) == "fr"
        assert t("factor.zone_inhabituelle", "en") == "Unusual geographical zone"


# ---------------------------------------------------------------------------
# 5. Géographie : signal comportemental, jamais national
# ---------------------------------------------------------------------------
class TestLocationSignalIsBehavioral:
    def payment(self, user: User, city: str, country: str, coords, hours_after_history: int):
        from app.services.behavior import to_local

        last_local = to_local(now_utc(), user.timezone)
        ts = (last_local + timedelta(hours=hours_after_history)).astimezone(timezone.utc).replace(tzinfo=None)
        return {
            "merchant": "Commerçant local",
            "amount": 25.0,
            "category": "Alimentation",
            "city": city,
            "country": country,
            "channel": "carte",
            "device_fingerprint": "fp-appareil-de-confiance",
            "device_label": "Portable habituel",
            "latitude": coords[0],
            "longitude": coords[1],
            "ts": ts,
        }

    def test_zone_factor_is_relative_to_the_account_history(self, db, rules_only):
        # Client habitué à Paris : une opération depuis Lagos est une première
        # pour **son compte** -> signal comportemental (et non « pays suspect »).
        paris_user, paris_profile = make_user_with_history(
            db, "paris@example.com", "Paris", "France", PARIS
        )
        assessment = assess_payment(db, paris_user, self.payment(paris_user, "Lagos", "Nigéria", LAGOS, 240), paris_profile)
        codes = {factor["code"] for factor in assessment.factors}
        assert "zone_inhabituelle" in codes
        assert "pays_etranger" not in codes  # ancien code de suspicion par pays supprimé
        detail = next(factor["detail"] for factor in assessment.factors if factor["code"] == "zone_inhabituelle")
        assert "historique" in detail and "origine" in detail
        assert "zone_inhabituelle" in assessment.step_up

        # Client habitué à Lagos : la même ville ne déclenche rien.
        lagos_user, lagos_profile = make_user_with_history(
            db, "lagos@example.com", "Lagos", "Nigéria", LAGOS
        )
        assessment = assess_payment(db, lagos_user, self.payment(lagos_user, "Lagos", "Nigéria", LAGOS, 240), lagos_profile)
        codes = {factor["code"] for factor in assessment.factors}
        assert "zone_inhabituelle" not in codes

    def test_no_factor_depends_on_french_nationality(self, db, rules_only):
        """Un client hors France n'est jamais pénalisé pour son pays en soi."""
        user, profile = make_user_with_history(
            db, "newyork@example.com", "New York", "États-Unis", (40.7128, -74.0060)
        )
        assessment = assess_payment(
            db, user, self.payment(user, "New York", "États-Unis", (40.7128, -74.0060), 240), profile
        )
        codes = {factor["code"] for factor in assessment.factors}
        assert "zone_inhabituelle" not in codes
        assert "pays_etranger" not in codes
        assert assessment.decision in {"accept", "verify"}
