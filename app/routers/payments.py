"""
Paiement électronique sécurisé par l'IA — couche HTTP.

Déroulé en trois appels
-----------------------
1. `POST /api/payments/quote`  : analyse de risque → score, décision, facteurs.
   Si la décision n'est pas « block », un **code à usage unique** est généré
   (60 secondes, usage unique, vérifié côté serveur).
2. `GET  /api/payments/challenge/{id}` : état du défi (compte à rebours).
3. `POST /api/payments/confirm` : vérification du code (+ code PIN si le risque
   est élevé) puis exécution de l'opération simulée.

La logique métier vit dans `app/services/payment_flow.py`, partagée avec
l'interface Streamlit : une seule implémentation, deux interfaces.

Aucune donnée de carte n'est demandée ni transmise : le prototype ne simule
aucun prélèvement réel.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.database import get_db
from app.deps import client_ip, get_current_user
from app.models import User
from app.schemas import (
    ChallengeStatusOut,
    PaymentConfirmRequest,
    PaymentQuoteRequest,
    PaymentQuoteResponse,
    PaymentResultOut,
)
from app.services import payment_flow
from app.services.otp import attempts_left, seconds_remaining
from app.services.payment_flow import demo_scenarios, load_challenge, payment_context

router = APIRouter(prefix="/api/payments", tags=["paiement sécurisé"])


# ---------------------------------------------------------------------------
# Contexte et scénarios de démonstration
# ---------------------------------------------------------------------------
@router.get("/context")
def context(
    user: User = Depends(get_current_user), db: Session = Depends(get_db)
) -> dict:
    """Informations nécessaires au formulaire de paiement."""
    return payment_context(db, user)


@router.get("/scenarios")
def scenarios(user: User = Depends(get_current_user)) -> dict:
    """Scénarios prêts à l'emploi pour la démonstration."""
    return {"scenarios": demo_scenarios(user)}


# ---------------------------------------------------------------------------
# 1. Analyse de risque et génération du code à usage unique
# ---------------------------------------------------------------------------
@router.post("/quote", response_model=PaymentQuoteResponse)
def quote_payment(
    payload: PaymentQuoteRequest,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> PaymentQuoteResponse:
    """Analyse le risque du paiement puis remet un code à usage unique (60 s)."""
    result = payment_flow.quote(
        db,
        user,
        payload.model_dump(),
        ip_address=client_ip(request),
    )
    return PaymentQuoteResponse(**result)


# ---------------------------------------------------------------------------
# 2. État du défi (compte à rebours côté client)
# ---------------------------------------------------------------------------
@router.get("/challenge/{challenge_id}", response_model=ChallengeStatusOut)
def challenge_status(
    challenge_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ChallengeStatusOut:
    """Secondes restantes, tentatives restantes et état du défi."""
    challenge = load_challenge(db, user, challenge_id)
    if challenge is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Demande de paiement introuvable.",
        )
    remaining = seconds_remaining(challenge)
    return ChallengeStatusOut(
        challenge_id=challenge.id,
        expires_at=challenge.expires_at,
        seconds_remaining=remaining,
        attempts_left=attempts_left(challenge),
        consumed=challenge.consumed_at is not None,
        expired=remaining == 0,
        decision=challenge.decision,
        require_pin=challenge.require_pin,
        amount=float((challenge.payload or {}).get("amount", 0.0)),
        merchant=str((challenge.payload or {}).get("merchant", "")),
    )


@router.post("/challenge/{challenge_id}/cancel")
def cancel_challenge(
    challenge_id: int,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """Annule un défi en cours (l'utilisateur renonce au paiement)."""
    challenge = load_challenge(db, user, challenge_id)
    if challenge is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Demande de paiement introuvable.",
        )
    payment_flow.cancel(db, challenge, user.id, ip_address=client_ip(request))
    return {"status": "ok", "message": "Paiement annulé."}


# ---------------------------------------------------------------------------
# 3. Validation du code et exécution du paiement
# ---------------------------------------------------------------------------
@router.post("/confirm", response_model=PaymentResultOut)
def confirm_payment(
    payload: PaymentConfirmRequest,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> PaymentResultOut:
    """Vérifie le code à usage unique puis exécute le paiement simulé."""
    challenge = load_challenge(db, user, payload.challenge_id)
    if challenge is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Demande de paiement introuvable.",
        )

    result = payment_flow.confirm(
        db,
        user,
        challenge,
        code=payload.code,
        pin=payload.pin,
        ip_address=client_ip(request),
    )
    if result["status"] == "error":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=result["message"],
        )
    return PaymentResultOut(**result)
