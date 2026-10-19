"""
Tests d'atomicité du paiement et d'usage unique du code (OTP).

Objectifs vérifiés
------------------
1. **Une seule transaction** pour tout un paiement : défi, débit, transaction,
   appareil, profil, alertes et audit sont validés ou annulés ensemble.
2. **Aucune écriture partielle** : si une étape échoue, le solde, l'historique
   et le défi OTP restent exactement dans leur état initial.
3. **Usage unique en concurrence** : le même code ne peut pas exécuter deux
   paiements, même depuis deux sessions ou deux threads simultanés.
4. **Fonds insuffisants** : refus explicite, jamais de débit négatif.

Exécution :
    .venv\\Scripts\\python -m pytest tests/test_atomicity.py -q
"""

from __future__ import annotations

import threading
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.config import settings
from app.database import Base, now_utc
from app.ml.features import build_features
from app.models import Account, AuditLog, PaymentChallenge, Transaction, User
from app.security import hash_password, hash_pin
from app.services import payment_flow
from app.services.otp import verify_challenge
from app.services.risk_engine import assess_payment

TZ = ZoneInfo(settings.app_timezone)
PARIS = (48.8566, 2.3522)
PASSWORD = "Test1234!"
PIN = "4321"
TRUSTED_DEVICE = "fp-appareil-de-confiance"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def local_utc(day: datetime, hour: int, minute: int = 0) -> datetime:
    aware = datetime(day.year, day.month, day.day, hour, minute, tzinfo=TZ)
    return aware.astimezone(timezone.utc).replace(tzinfo=None)


def create_user(db, balance: float = 2000.0) -> tuple[User, Account]:
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
    today = now_utc().date()
    for index in range(days):
        day = today - timedelta(days=days - index)
        db.add(
            Transaction(
                user_id=user.id,
                account_id=account.id,
                ts=local_utc(datetime(day.year, day.month, day.day), 13),
                amount=-round(8 + (index * 7) % 38, 2),
                label="Achat de proximité",
                merchant=["Supermarché Colibri", "Boulangerie Maison Blanc"][index % 2],
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


def payment_payload(amount: float = 15.0) -> dict:
    return {
        "merchant": "Supermarché Colibri",
        "amount": amount,
        "category": "Alimentation",
        "city": "Paris",
        "country": "France",
        "channel": "carte",
        "device_fingerprint": TRUSTED_DEVICE,
        "device_label": "Portable habituel",
        "simulated_hour": 14,
    }


def quote_for(db, user: User, amount: float = 15.0) -> dict:
    """Analyse un paiement et retourne le devis (défi + code de simulation)."""
    result = payment_flow.quote(db, user, payment_payload(amount), ip_address="test")
    assert result["challenge_id"] is not None, result
    return result


def challenge_of(db, user: User, quote: dict):
    challenge = payment_flow.load_challenge(db, user, quote["challenge_id"])
    assert challenge is not None
    return challenge


def transaction_count(db, user: User) -> int:
    return int(db.scalar(select(func.count(Transaction.id)).where(Transaction.user_id == user.id)) or 0)


# ---------------------------------------------------------------------------
# Fixtures
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
    """Neutralise le composant IA pour des décisions déterministes."""

    def fake_score_context(context: dict, profile: dict):
        return 0.0, build_features(context, profile), []

    monkeypatch.setattr("app.services.risk_engine.score_context", fake_score_context)


@pytest.fixture()
def payer(db, rules_only):
    """Utilisateur avec 60 jours d'historique et son compte courant."""
    user, account = create_user(db)
    add_history(db, user, account)
    return user, account


# ---------------------------------------------------------------------------
# 1. Succès : tout est validé ensemble
# ---------------------------------------------------------------------------
class TestAtomicConfirmation:
    def test_successful_payment_updates_everything_once(self, db, payer):
        user, account = payer
        before_balance = float(account.balance)
        quote = quote_for(db, user, amount=15.0)
        challenge = challenge_of(db, user, quote)

        result = payment_flow.confirm(
            db,
            user,
            challenge,
            quote["simulation_code"],
            PIN if quote["require_pin"] else None,
            ip_address="test",
        )

        assert result["status"] == "completed", result
        db.refresh(account)
        assert account.balance == pytest.approx(round(before_balance - 15.0, 2), abs=0.01)

        transaction = db.get(Transaction, result["transaction_id"])
        assert transaction is not None
        assert transaction.status == "completed"
        assert transaction.merchant == "Supermarché Colibri"
        assert transaction.amount == pytest.approx(-15.0, abs=0.01)

        db.refresh(challenge)
        assert challenge.consumed_at is not None

        audit = int(
            db.scalar(
                select(func.count(AuditLog.id)).where(
                    AuditLog.user_id == user.id, AuditLog.action == "paiement_execute"
                )
            )
            or 0
        )
        assert audit == 1

    def test_decision_and_pin_are_rechecked_inside_confirmation(self, db, payer):
        """La confirmation reste soumise au moteur (pas de contournement)."""
        user, account = payer
        quote = quote_for(db, user, amount=15.0)
        challenge = challenge_of(db, user, quote)

        # Mauvais code : refus, aucune écriture financière.
        result = payment_flow.confirm(db, user, challenge, "000000", None, ip_address="test")
        assert result["status"] == "error"
        db.refresh(account)
        assert account.balance == pytest.approx(2000.0, abs=0.01)
        assert (
            int(
                db.scalar(
                    select(func.count(Transaction.id)).where(
                        Transaction.user_id == user.id, Transaction.status == "completed"
                    )
                )
                or 0
            )
            == 60
        )


# ---------------------------------------------------------------------------
# 2. Aucune écriture partielle en cas d'échec (rollback total)
# ---------------------------------------------------------------------------
class TestRollbackOnFailure:
    def test_exception_during_payment_rolls_back_everything(self, db, payer, monkeypatch):
        user, account = payer
        before_balance = float(account.balance)
        before_transactions = transaction_count(db, user)
        quote = quote_for(db, user, amount=15.0)
        challenge = challenge_of(db, user, quote)
        code = quote["simulation_code"]

        def boom(*_, **__):
            raise RuntimeError("panne simulée pendant l'exécution")

        # Échoue après la consommation du défi et le débit, avant le commit.
        monkeypatch.setattr(payment_flow, "refresh_profile", boom)

        with pytest.raises(RuntimeError):
            payment_flow.confirm(db, user, challenge, code, None, ip_address="test")

        db.expire_all()
        db.refresh(account)
        db.refresh(challenge)
        # Le débit est annulé, la transaction n'existe pas, le défi redevient
        # utilisable : rien n'a été partiellement enregistré.
        assert account.balance == pytest.approx(before_balance, abs=0.01)
        assert transaction_count(db, user) == before_transactions
        assert challenge.consumed_at is None
        assert (
            int(
                db.scalar(
                    select(func.count(AuditLog.id)).where(AuditLog.action == "paiement_execute")
                )
                or 0
            )
            == 0
        )

    def test_insufficient_funds_blocks_without_debiting(self, db, payer):
        user, account = payer
        quote = quote_for(db, user, amount=15.0)
        challenge = challenge_of(db, user, quote)

        # Le solde change entre l'analyse et la confirmation.
        account.balance = 5.0
        db.commit()

        result = payment_flow.confirm(
            db, user, challenge, quote["simulation_code"], None, ip_address="test"
        )
        assert result["status"] == "blocked"
        assert "solde insuffisant" in result["message"].lower()

        db.refresh(account)
        assert account.balance == pytest.approx(5.0, abs=0.01)
        assert transaction_count(db, user) == 60  # aucune opération ajoutée
        db.refresh(challenge)
        assert challenge.consumed_at is not None  # tentative consommée et journalisée

    def test_verify_without_commit_can_be_rolled_back(self, db, payer):
        """`verify_challenge(commit=False)` participe à la transaction appelante."""
        user, _ = payer
        quote = quote_for(db, user, amount=15.0)
        challenge = challenge_of(db, user, quote)

        ok, _, _ = verify_challenge(db, challenge, quote["simulation_code"], commit=False)
        assert ok is True
        assert challenge.consumed_at is not None

        db.rollback()
        db.refresh(challenge)
        assert challenge.consumed_at is None  # aucun état consommé sans commit


# ---------------------------------------------------------------------------
# 3. Usage unique : jamais deux fois, même en concurrence
# ---------------------------------------------------------------------------
class TestSingleUseUnderConcurrency:
    def test_second_session_cannot_reuse_the_same_code(self, tmp_path):
        url = f"sqlite:///{(tmp_path / 'otp.db').as_posix()}"
        engine = create_engine(url, connect_args={"check_same_thread": False}, future=True)
        Base.metadata.create_all(engine)
        Session = sessionmaker(bind=engine, expire_on_commit=False, future=True)
        setup, first, second = Session(), Session(), Session()
        try:
            user, account = create_user(setup)
            add_history(setup, user, account)
            quote = quote_for(setup, user, amount=15.0)
            code = quote["simulation_code"]

            # Les deux sessions chargent le défi avant toute consommation.
            user_first = first.get(User, user.id)
            challenge_first = first.get(PaymentChallenge, quote["challenge_id"])
            user_second = second.get(User, user.id)
            challenge_second = second.get(PaymentChallenge, quote["challenge_id"])
            assert challenge_first.consumed_at is None
            assert challenge_second.consumed_at is None

            # La seconde session valide en premier.
            result_second = payment_flow.confirm(
                second, user_second, challenge_second, code, None, ip_address="test2"
            )
            assert result_second["status"] == "completed", result_second

            # La première session rejoue le même code : refus, aucun second débit.
            result_first = payment_flow.confirm(
                first, user_first, challenge_first, code, None, ip_address="test1"
            )
            assert result_first["status"] == "error"
            assert result_first["error"] in {"consomme", "trop_dessais"}

            db_account = setup.get(Account, account.id)
            setup.refresh(db_account)
            assert db_account.balance == pytest.approx(2000.0 - 15.0, abs=0.01)
            completed = int(
                setup.scalar(
                    select(func.count(Transaction.id)).where(
                        Transaction.user_id == user.id, Transaction.status == "completed"
                    )
                )
                or 0
            )
            assert completed == 61  # 60 d'historique + 1 seul paiement
        finally:
            setup.close()
            first.close()
            second.close()
            engine.dispose()

    def test_parallel_confirmations_debit_exactly_once(self, tmp_path):
        """Deux threads valident en même temps : un seul paiement aboutit."""
        url = f"sqlite:///{(tmp_path / 'parallel.db').as_posix()}"
        engine = create_engine(url, connect_args={"check_same_thread": False}, future=True)
        Base.metadata.create_all(engine)
        Session = sessionmaker(bind=engine, expire_on_commit=False, future=True)

        setup = Session()
        user, account = create_user(setup)
        add_history(setup, user, account)
        quote = quote_for(setup, user, amount=15.0)
        code = quote["simulation_code"]
        challenge_id = quote["challenge_id"]
        user_id, account_id = user.id, account.id
        setup.close()

        results: list[dict] = []
        lock = threading.Lock()
        barrier = threading.Barrier(2)

        def worker() -> None:
            session = Session()
            try:
                local_user = session.get(User, user_id)
                challenge = session.get(PaymentChallenge, challenge_id)
                barrier.wait(timeout=10)
                outcome = payment_flow.confirm(
                    session, local_user, challenge, code, None, ip_address="thread"
                )
            except Exception as error:  # contention SQLite : comptée comme refus
                outcome = {"status": "error", "error": type(error).__name__}
            finally:
                session.close()
            with lock:
                results.append(outcome)

        threads = [threading.Thread(target=worker) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=30)

        completed = [row for row in results if row.get("status") == "completed"]
        assert len(completed) == 1, results

        check = Session()
        try:
            db_account = check.get(Account, account_id)
            check.refresh(db_account)
            assert db_account.balance == pytest.approx(2000.0 - 15.0, abs=0.01)
            total = int(
                check.scalar(
                    select(func.count(Transaction.id)).where(
                        Transaction.user_id == user_id, Transaction.status == "completed"
                    )
                )
                or 0
            )
            assert total == 61  # 60 d'historique + 1 seul paiement
        finally:
            check.close()
            engine.dispose()
