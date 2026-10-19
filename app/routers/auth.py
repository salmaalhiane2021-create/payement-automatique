"""
Authentification : inscription, connexion, session, code PIN.

Sécurité appliquée
------------------
* mots de passe hachés en bcrypt (jamais stockés ni journalisés en clair) ;
* politique de mot de passe vérifiée côté serveur ;
* limitation des tentatives de connexion (verrouillage temporaire du compte) ;
* message d'erreur identique que l'adresse existe ou non (anti-énumération) ;
* journalisation d'audit de toutes les tentatives.
"""

from __future__ import annotations

from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db, now_utc
from app.deps import client_ip, get_current_user, user_agent
from app.models import Account, Device, User
from app.schemas import LoginRequest, LoginResponse, RegisterRequest, UserOut
from app.security import (
    create_access_token,
    generate_device_fingerprint,
    hash_password,
    hash_pin,
    validate_pin_format,
    validate_password_strength,
    verify_password,
)
from app.services.alerts import log_action

router = APIRouter(prefix="/api/auth", tags=["authentification"])

# Message unique pour tous les échecs de connexion (anti-énumération des comptes)
INVALID_CREDENTIALS = "Adresse e-mail ou mot de passe incorrect."


def _normalise_email(email: str) -> str:
    return email.strip().lower()


@router.post("/register", response_model=LoginResponse, status_code=status.HTTP_201_CREATED)
def register(payload: RegisterRequest, request: Request, db: Session = Depends(get_db)) -> LoginResponse:
    """Crée un compte utilisateur et ouvre ses comptes bancaires simulés."""
    email = _normalise_email(payload.email)

    is_valid, message = validate_password_strength(payload.password)
    if not is_valid:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=message)
    if not validate_pin_format(payload.pin):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Le code PIN doit contenir 4 à 6 chiffres.")

    if db.scalar(select(User).where(User.email == email)):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Un compte existe déjà avec cette adresse.")

    user = User(
        full_name=payload.full_name,
        email=email,
        password_hash=hash_password(payload.password),
        pin_hash=hash_pin(payload.pin),
        phone=payload.phone,
        city=payload.city or "Paris",
        country="France",
        monthly_income=2500.0,
        created_at=now_utc(),
    )
    db.add(user)
    db.flush()

    # Comptes simulés : un compte courant doté d'un solde de démonstration
    # et un livret d'épargne vide (l'objectif est de le remplir).
    db.add_all(
        [
            Account(
                user_id=user.id,
                kind="courant",
                label="Compte courant",
                iban_masked=f"FR76 3000 {user.id:04d} 0000 0000",
                balance=1800.0,
            ),
            Account(
                user_id=user.id,
                kind="epargne",
                label="Livret d'épargne",
                iban_masked=f"FR76 3001 {user.id:04d} 0000 0000",
                balance=0.0,
            ),
        ]
    )
    db.add(
        Device(
            user_id=user.id,
            fingerprint=generate_device_fingerprint(user.id, "appareil-inscription"),
            label="Appareil d'inscription",
            platform="Navigateur",
            is_trusted=True,
        )
    )
    db.commit()

    log_action(db, action="inscription", user_id=user.id, detail="Nouveau compte créé.", ip_address=client_ip(request))

    token, expires_in = create_access_token(user.id, user.email)
    return LoginResponse(access_token=token, expires_in=expires_in, user=UserOut.model_validate(user))


@router.post("/login", response_model=LoginResponse)
def login(payload: LoginRequest, request: Request, db: Session = Depends(get_db)) -> LoginResponse:
    """Authentifie un utilisateur et renvoie un jeton de session."""
    email = _normalise_email(payload.email)
    ip = client_ip(request)
    agent = user_agent(request)

    user = db.scalar(select(User).where(User.email == email))

    # Verrouillage temporaire après trop d'échecs (protection force brute)
    if user and user.locked_until and user.locked_until > now_utc():
        remaining = int((user.locked_until - now_utc()).total_seconds() // 60) + 1
        log_action(db, "connexion", user.id, "bloque", f"Compte verrouillé ({remaining} min).", ip, agent)
        raise HTTPException(
            status_code=status.HTTP_423_LOCKED,
            detail=f"Compte temporairement verrouillé après trop de tentatives. Réessayez dans {remaining} minute(s).",
        )

    if user is None or not verify_password(payload.password, user.password_hash):
        if user is not None:
            user.failed_attempts = (user.failed_attempts or 0) + 1
            if user.failed_attempts >= settings.login_max_attempts:
                user.locked_until = now_utc() + timedelta(minutes=settings.login_lockout_minutes)
                user.failed_attempts = 0
                detail = f"Compte verrouillé pour {settings.login_lockout_minutes} minutes."
            else:
                detail = f"Échec de connexion ({user.failed_attempts}/{settings.login_max_attempts})."
            db.commit()
            log_action(db, "connexion", user.id, "echec", detail, ip, agent)
        else:
            log_action(db, "connexion", None, "echec", f"Adresse inconnue : {email}", ip, agent)

        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=INVALID_CREDENTIALS)

    if not user.is_active:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Ce compte est désactivé.")

    # Connexion réussie
    user.failed_attempts = 0
    user.locked_until = None
    user.last_login_at = now_utc()
    db.commit()

    log_action(db, "connexion", user.id, "ok", "Connexion réussie.", ip, agent)

    token, expires_in = create_access_token(user.id, user.email)
    return LoginResponse(access_token=token, expires_in=expires_in, user=UserOut.model_validate(user))


@router.get("/me", response_model=UserOut)
def me(user: User = Depends(get_current_user)) -> UserOut:
    """Retourne le profil de l'utilisateur connecté."""
    return UserOut.model_validate(user)


@router.post("/logout")
def logout(
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """
    Déconnexion.

    Le jeton JWT est sans état : la déconnexion consiste à l'effacer côté
    client. On journalise l'événement pour l'audit.
    """
    log_action(db, "deconnexion", user.id, "ok", "Déconnexion.", client_ip(request))
    return {"status": "ok", "message": "Vous êtes déconnecté."}


@router.post("/pin")
def update_pin(
    request: Request,
    payload: dict,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """
    Définit ou modifie le code PIN (second facteur des paiements sensibles).

    Le mot de passe actuel est exigé : un jeton volé ne suffit donc pas à
    modifier le second facteur.
    """
    password = str(payload.get("password", ""))
    pin = str(payload.get("pin", ""))
    ip = client_ip(request)

    if not verify_password(password, user.password_hash):
        log_action(db, "changement_pin", user.id, "echec", "Mot de passe incorrect.", ip)
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Mot de passe incorrect.")
    if not validate_pin_format(pin):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Le code PIN doit contenir 4 à 6 chiffres.")

    user.pin_hash = hash_pin(pin)
    db.commit()
    log_action(db, "changement_pin", user.id, "ok", "Code PIN mis à jour.", ip)
    return {"status": "ok", "message": "Votre code PIN a été mis à jour."}
