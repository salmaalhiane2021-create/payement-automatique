"""
Tests unitaires du cœur de NexPay AI.

Couverture
----------
1. **Sécurité** — hachage bcrypt des mots de passe / PIN, jetons JWT,
   politique de robustesse du mot de passe, empreintes OTP liées au défi.
2. **Authentification dynamique** — code à usage unique 60 s : usage unique,
   expiration, verrouillage après 3 tentatives, second facteur PIN.
3. **Moteur de risque** — monotonie du score, appareil inconnu (step-up),
   fonds insuffisants, déplacement impossible, décisions accept/verify/block.
4. **Profil comportemental** — détection des abonnements récurrents.

Exécution :
    .venv\\Scripts\\python -m pytest tests -q

Les tests s'exécutent sur une base SQLite **en mémoire** : le fichier de
démonstration `data/nexpay.db` n'est jamais modifié.
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
from app.ml.anomaly import quick_statistical_risk
from app.ml.features import build_features
from app.models import Account, Transaction, User
from app.security import (
    create_access_token,
    decode_access_token,
    generate_device_fingerprint,
    hash_otp_code,
    hash_password,
    hash_pin,
    validate_password_strength,
    validate_pin_format,
    verify_otp_code,
    verify_password,
    verify_pin,
)
from app.services.behavior import build_profile, detect_subscriptions
from app.services.otp import (
    ERROR_CONSUMED,
    ERROR_EXPIRED,
    ERROR_INVALID,
    ERROR_PIN,
    ERROR_TOO_MANY,
    create_challenge,
    masked_phone,
    purge_expired,
    seconds_remaining,
    verify_challenge,
)
from app.services.risk_engine import assess_payment

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
TZ = ZoneInfo(settings.app_timezone)
PARIS = (48.8566, 2.3522)
SYDNEY = (-33.8688, 151.2093)
TRUSTED_DEVICE = "fp-appareil-de-confiance"
PASSWORD = "Test1234!"
PIN = "4321"


def local_utc(day: datetime, hour: int, minute: int = 0) -> datetime:
    """Heure locale (Europe/Paris) → UTC naïf, comme le fait l'application."""
    aware = datetime(day.year, day.month, day.day, hour, minute, tzinfo=TZ)
    return aware.astimezone(timezone.utc).replace(tzinfo=None)


def create_user(db, balance: float = 2000.0) -> tuple[User, Account]:
    """Utilisateur de test complet : mot de passe, PIN et compte courant."""
    user = User(
        full_name="Camille Test",
        email="camille.test@nexpay.fr",
        password_hash=hash_password(PASSWORD),
        pin_hash=hash_pin(PIN),
        phone="0611223344",
        city="Paris",
        country="France",
        monthly_income=2800.0,
    )
    db.add(user)
    db.flush()
    account = Account(user_id=user.id, kind="courant", label="Compte courant", balance=balance)
    db.add(account)
    db.commit()
    return user, account


def add_history(db, user: User, account: Account, days: int = 60) -> None:
    """Historique « ordinaire » : un paiement par jour à 13h, 8–45 €, Paris."""
    today = now_utc().date()
    merchants = ["Supermarché Colibri", "Boulangerie Maison Blanc", "Café des Arts"]
    for index in range(days):
        day = today - timedelta(days=days - index)
        db.add(
            Transaction(
                user_id=user.id,
                account_id=account.id,
                ts=local_utc(datetime(day.year, day.month, day.day), 13),
                amount=-round(8 + (index * 7) % 38, 2),
                label="Achat de proximité",
                merchant=merchants[index % len(merchants)],
                category="Alimentation",
                channel="carte",
                city="Paris",
                country="France",
                latitude=PARIS[0],
                longitude=PARIS[1],
                device_fingerprint=TRUSTED_DEVICE,
                status="completed",
            )
        )
    db.commit()


def last_transaction_ts(db, user: User) -> datetime:
    """Horodatage de la dernière opération en base (UTC naïf)."""
    row = db.scalars(
        select(Transaction).where(Transaction.user_id == user.id).order_by(Transaction.ts.desc()).limit(1)
    ).first()
    assert row is not None
    return row.ts


def base_payment(hour: int = 14, minute: int = 0, **overrides) -> dict:
    """Contexte de paiement « normal » (surchargeable champ par champ)."""
    today = now_utc().date()
    payment = {
        "merchant": "Supermarché Colibri",
        "amount": 15.0,
        "category": "Alimentation",
        "city": "Paris",
        "country": "France",
        "channel": "carte",
        "device_fingerprint": TRUSTED_DEVICE,
        "device_label": "Portable habituel",
        "ts": local_utc(today, hour, minute),
    }
    payment.update(overrides)
    return payment


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
@pytest.fixture()
def db():
    """Base SQLite en mémoire, isolée pour chaque test."""
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
    """
    Neutralise le composant IA du moteur de risque.

    Les tests des règles doivent être déterministes : le score d'anomalie
    dépend d'un modèle entraîné sur les données de démonstration, absent sur
    une machine fraîchement clonée.
    """
    def fake_score_context(context: dict, profile: dict):
        return 0.0, build_features(context, profile), []

    monkeypatch.setattr("app.services.risk_engine.score_context", fake_score_context)


@pytest.fixture()
def demo_user(db):
    """Utilisateur avec 60 jours d'historique et son profil comportemental."""
    user, account = create_user(db)
    add_history(db, user, account)
    return user, account, build_profile(db, user)


# ---------------------------------------------------------------------------
# 1. Sécurité : mots de passe, PIN, JWT, OTP
# ---------------------------------------------------------------------------
class TestPasswordSecurity:
    def test_bcrypt_hash_verifies_and_never_stores_plaintext(self):
        hashed = hash_password(PASSWORD)
        assert hashed != PASSWORD
        assert hashed.startswith("$2b$")          # format bcrypt
        assert verify_password(PASSWORD, hashed) is True
        assert verify_password("Mauvais123", hashed) is False
        assert verify_password(PASSWORD, None) is False

    def test_pin_hash_separate_namespace(self):
        pin_hash = hash_pin(PIN)
        assert verify_pin(PIN, pin_hash) is True
        assert verify_pin("0000", pin_hash) is False
        # Un hash de mot de passe ne doit pas valider un PIN équivalent.
        assert verify_pin(PASSWORD, hash_password(PASSWORD)) is False

    @pytest.mark.parametrize(
        "password",
        ["court1", "sanschiffre", "12345678", "azerty123", "pass\nword1"],
    )
    def test_password_policy_rejects_weak_passwords(self, password):
        valid, message = validate_password_strength(password)
        assert valid is False
        assert message

    def test_password_policy_accepts_strong_password(self):
        valid, message = validate_password_strength(PASSWORD)
        assert valid is True
        assert message == ""

    def test_pin_format(self):
        assert validate_pin_format("4321") is True
        assert validate_pin_format("123456") is True
        assert validate_pin_format("12") is False
        assert validate_pin_format("12ab") is False

    def test_access_token_roundtrip_and_rejects_tampering(self):
        token, expires_in = create_access_token(user_id=7, email="camille@nexpay.fr")
        assert expires_in == settings.access_token_expire_minutes * 60
        payload = decode_access_token(token)
        assert payload is not None
        assert payload["sub"] == "7"
        assert payload["email"] == "camille@nexpay.fr"
        assert decode_access_token("jeton.bidon.xxx") is None

    def test_device_fingerprint_is_unique_hex(self):
        first = generate_device_fingerprint(1, "Test")
        second = generate_device_fingerprint(1, "Test")
        assert first != second
        assert len(first) == 32
        int(first, 16)  # hexadécimal valide

    def test_otp_hash_is_bound_to_challenge(self):
        fingerprint = hash_otp_code(10, "123456")
        assert verify_otp_code(10, "123456", fingerprint) is True
        assert verify_otp_code(11, "123456", fingerprint) is False
        assert verify_otp_code(10, "654321", fingerprint) is False


# ---------------------------------------------------------------------------
# 2. Authentification dynamique : OTP 60 s à usage unique
# ---------------------------------------------------------------------------
class TestOtpLifecycle:
    def test_code_is_six_digits_and_not_stored_plaintext(self, db):
        user, _ = create_user(db)
        challenge, code = create_challenge(db, user, {"montant": 15.0}, 5.0, "accept", require_pin=False)
        assert len(code) == 6 and code.isdigit()
        assert challenge.code_hash != code
        assert challenge.consumed_at is None
        assert 55 <= seconds_remaining(challenge) <= 60

    def test_correct_code_is_single_use(self, db):
        user, _ = create_user(db)
        challenge, code = create_challenge(db, user, {"montant": 15.0}, 5.0, "accept", require_pin=False)
        ok, error, _ = verify_challenge(db, challenge, code)
        assert ok is True and error == ""
        # Rejouer le même code doit échouer : usage unique.
        ok, error, message = verify_challenge(db, challenge, code)
        assert ok is False
        assert error == ERROR_CONSUMED
        assert "déjà été utilisé" in message

    def test_lockout_after_three_wrong_attempts(self, db):
        user, _ = create_user(db)
        challenge, code = create_challenge(db, user, {"montant": 15.0}, 5.0, "accept", require_pin=False)
        for expected_remaining in (2, 1):
            ok, error, message = verify_challenge(db, challenge, "000000" if code != "000000" else "111111")
            assert ok is False and error == ERROR_INVALID
            assert f"{expected_remaining} tentative(s)" in message
        ok, error, _ = verify_challenge(db, challenge, "000000" if code != "000000" else "111111")
        assert ok is False and error == ERROR_TOO_MANY
        # Même avec le bon code, le défi est invalidé.
        ok, error, message = verify_challenge(db, challenge, code)
        assert ok is False
        assert error == ERROR_TOO_MANY
        assert "invalidé" in message

    def test_expired_code_is_rejected_before_consumption(self, db):
        user, _ = create_user(db)
        challenge, code = create_challenge(db, user, {"montant": 15.0}, 5.0, "accept", require_pin=False)
        challenge.expires_at = now_utc() - timedelta(seconds=1)
        db.commit()
        ok, error, message = verify_challenge(db, challenge, code)
        assert ok is False
        assert error == ERROR_EXPIRED
        assert "expiré" in message

    def test_expired_challenges_are_purged(self, db):
        user, _ = create_user(db)
        challenge, code = create_challenge(db, user, {"montant": 15.0}, 5.0, "accept", require_pin=False)
        challenge.expires_at = now_utc() - timedelta(seconds=1)
        db.commit()
        assert purge_expired(db) == 1
        # Un défi purgé répond « expiré » (message plus juste qu'« déjà utilisé »).
        ok, error, _ = verify_challenge(db, challenge, code)
        assert ok is False and error == ERROR_EXPIRED

    def test_verify_decision_requires_pin(self, db):
        user, _ = create_user(db)
        challenge, code = create_challenge(db, user, {"montant": 850.0}, 50.0, "verify", require_pin=True)
        # Bon code OTP mais PIN absent : refus.
        ok, error, message = verify_challenge(db, challenge, code, pin=None)
        assert ok is False and error == ERROR_PIN
        assert "PIN requis" in message
        # Bon code OTP mais PIN erroné : refus + une tentative consommée.
        ok, error, _ = verify_challenge(db, challenge, code, pin="9999")
        assert ok is False and error == ERROR_PIN
        assert challenge.attempts == 1
        # Code OTP + PIN corrects : succès.
        ok, error, _ = verify_challenge(db, challenge, code, pin=PIN)
        assert ok is True and error == ""

    def test_masked_phone(self):
        assert masked_phone("0611223344") == "06••••••44"
        assert masked_phone("") == "numéro non renseigné"


# ---------------------------------------------------------------------------
# 3. Moteur de risque
# ---------------------------------------------------------------------------
class TestRiskEngine:
    def test_usual_payment_is_accepted(self, db, demo_user, rules_only):
        user, _, profile = demo_user
        assessment = assess_payment(db, user, base_payment(hour=14), profile)
        assert assessment.decision == "accept"
        assert assessment.score < settings.risk_accept_max
        assert assessment.hard_block is False

    def test_score_grows_with_amount_and_stays_below_block(self, db, demo_user, rules_only):
        user, _, profile = demo_user
        small = assess_payment(db, user, base_payment(amount=15.0), profile)
        big = assess_payment(db, user, base_payment(amount=1500.0), profile)
        # Monotonie : un montant hors normes score plus haut, sans blocage
        # automatique (le solde est suffisant et l'appareil est connu).
        assert small.score < big.score
        assert big.decision == "verify"
        assert "montant_inhabituel" in {factor["code"] for factor in big.factors}

    def test_night_payment_scores_higher_than_daytime(self, db, demo_user, rules_only):
        user, _, profile = demo_user
        day = assess_payment(db, user, base_payment(hour=14), profile)
        night = assess_payment(db, user, base_payment(hour=3), profile)
        assert night.score > day.score
        assert "heure_inhabituelle" in {factor["code"] for factor in night.factors}

    def test_unknown_device_forces_step_up_verification(self, db, demo_user, rules_only):
        user, _, profile = demo_user
        payment = base_payment(
            device_fingerprint="fp-jamais-vu",
            device_label="Navigateur non identifié",
        )
        assessment = assess_payment(db, user, payment, profile)
        codes = {factor["code"] for factor in assessment.factors}
        assert "appareil_inconnu" in codes
        # Un signal critique impose la double authentification, même si le
        # score seul n'atteint pas le seuil de vérification.
        assert assessment.decision == "verify"
        assert "appareil_inconnu" in assessment.step_up

    def test_insufficient_funds_is_hard_block(self, db, demo_user, rules_only):
        user, account, profile = demo_user
        assessment = assess_payment(db, user, base_payment(amount=account.balance + 1), profile)
        codes = {factor["code"] for factor in assessment.factors}
        assert assessment.hard_block is True
        assert assessment.decision == "block"
        assert "solde_insuffisant" in codes
        assert "refusé" in assessment.summary

    def test_impossible_travel_is_hard_block(self, db, demo_user, rules_only):
        user, _, profile = demo_user
        # Cinq minutes après un paiement à Paris, une opération à Sydney :
        # ~17 000 km en 5 minutes, soit plus de 200 000 km/h.
        payment = base_payment(
            merchant="Taxi Sydney",
            amount=30.0,
            city="Sydney",
            country="Australie",
        )
        payment["ts"] = last_transaction_ts(db, user) + timedelta(minutes=5)
        payment["latitude"], payment["longitude"] = SYDNEY
        assessment = assess_payment(db, user, payment, profile)
        codes = {factor["code"] for factor in assessment.factors}
        assert assessment.hard_block is True
        assert assessment.decision == "block"
        assert "velocite_impossible" in codes
        assert assessment.context["distance_km"] > 10_000
        assert assessment.context["velocity_kmh"] > 100_000

    def test_hard_block_beats_any_amount(self, db, demo_user, rules_only):
        """Déplacement impossible : le blocage s'applique même pour 2 €."""
        user, _, profile = demo_user
        payment = base_payment(city="Sydney", country="Australie", amount=2.0)
        payment["ts"] = last_transaction_ts(db, user) + timedelta(minutes=5)
        payment["latitude"], payment["longitude"] = SYDNEY
        assessment = assess_payment(db, user, payment, profile)
        assert assessment.decision == "block"

    def test_cold_start_profile_does_not_crash(self, db, rules_only):
        user, _ = create_user(db)  # aucun historique
        profile = build_profile(db, user)
        assert profile["cold_start"] is True
        assessment = assess_payment(db, user, base_payment(amount=120.0), profile)
        assert assessment.decision in {"accept", "verify", "block"}
        assert 0.0 <= assessment.score <= 100.0

    def test_statistical_risk_is_monotone(self, db, demo_user):
        _, _, profile = demo_user
        assert quick_statistical_risk(1500.0, profile) == 1.0
        assert quick_statistical_risk(15.0, profile) < 1.0
        # Compte sans historique : aucun signal statistique.
        assert quick_statistical_risk(1500.0, {"cold_start": True}) == 0.0


# ---------------------------------------------------------------------------
# 4. Profil comportemental : abonnements récurrents
# ---------------------------------------------------------------------------
class TestBehaviorProfile:
    def test_subscriptions_detected_but_rent_excluded(self, db):
        user, account = create_user(db)
        today = now_utc().date()
        for month_offset in range(5):
            day = today - timedelta(days=30 * month_offset)
            ts = local_utc(datetime(day.year, day.month, day.day), 9)
            for merchant, amount, category in (
                ("Streaming Plus", 12.99, "Abonnements"),
                ("Salle de sport", 29.9, "Loisirs"),
                ("Loyer", 900.0, "Logement"),
                ("Supermarché Colibri", 10.0 + month_offset * 7, "Alimentation"),
            ):
                db.add(
                    Transaction(
                        user_id=user.id,
                        account_id=account.id,
                        ts=ts,
                        amount=-amount,
                        merchant=merchant,
                        category=category,
                        channel="prelevement",
                        city="Paris",
                        country="France",
                        status="completed",
                    )
                )
        db.commit()

        profile = build_profile(db, user)
        names = [row["merchant"] for row in profile["subscriptions"]]
        assert "Streaming Plus" in names       # montant stable, 5 mois
        assert "Salle de sport" in names
        assert "Loyer" not in names            # récurrent mais non résiliable
        assert "Supermarché Colibri" not in names  # montants variables

    def test_blocked_and_fraud_transactions_are_excluded(self, db):
        user, account = create_user(db)
        today = now_utc().date()
        for index in range(12):
            day = today - timedelta(days=index)
            ts = local_utc(datetime(day.year, day.month, day.day), 13)
            db.add(
                Transaction(
                    user_id=user.id,
                    account_id=account.id,
                    ts=ts,
                    amount=-20.0,
                    merchant="Commerçant honnête",
                    category="Alimentation",
                    channel="carte",
                    city="Paris",
                    country="France",
                    status="completed",
                )
            )
        # Opération refusée et fraude : jamais apprises par le profil.
        db.add(
            Transaction(
                user_id=user.id,
                account_id=account.id,
                ts=local_utc(datetime(today.year, today.month, today.day), 3),
                amount=-900.0,
                merchant="Crypto Fantôme",
                category="Crypto",
                channel="en_ligne",
                city="Lagos",
                country="Nigéria",
                status="blocked",
                is_fraud=True,
            )
        )
        db.commit()

        profile = build_profile(db, user)
        assert profile["count"] == 12
        cities = profile["known_cities"]
        assert "Lagos" not in cities
        assert "Nigéria" not in profile["known_countries"]
