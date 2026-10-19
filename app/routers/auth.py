"""
Authentification : inscription, connexion, session, code PIN, préférences.

Sécurité appliquée
-----------------
* mots de passe hachés en bcrypt (jamais stockés ni journalisés en clair) ;
* politique de mot de passe vérifiée côté serveur ;
* limitation des tentatives de connexion (verrouillage temporaire du compte) ;
* message d'erreur identique que l'adresse existe ou non (anti-énumération) ;
* journalisation d'audit de toutes les tentatives.

Toute la logique métier vit dans `app/services/auth.py`, partagée avec
l'interface Streamlit : cette couche ne fait que traduire les résultats en
réponses HTTP.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.database import get_db
from app.deps import client_ip, get_current_user, request_language, user_agent
from app.models import User
from app.schemas import LoginRequest, LoginResponse, RegisterRequest, UpdateProfileRequest, UserOut
from app.security import create_access_token
from app.services import auth as auth_service

router = APIRouter(prefix="/api/auth", tags=["authentification"])


@router.post("/register", response_model=LoginResponse, status_code=status.HTTP_201_CREATED)
def register(payload: RegisterRequest, request: Request, db: Session = Depends(get_db)) -> LoginResponse:
    """Crée un compte utilisateur et ouvre ses comptes bancaires simulés."""
    result = auth_service.create_user_account(
        db,
        **payload.model_dump(),
        ip_address=client_ip(request),
        lang=request_language(request),
    )
    if not result.ok:
        code = status.HTTP_409_CONFLICT if result.error_code == "conflict" else status.HTTP_400_BAD_REQUEST
        raise HTTPException(status_code=code, detail=result.message)

    assert result.user is not None
    token, expires_in = create_access_token(result.user.id, result.user.email)
    return LoginResponse(access_token=token, expires_in=expires_in, user=UserOut.model_validate(result.user))


@router.post("/login", response_model=LoginResponse)
def login(payload: LoginRequest, request: Request, db: Session = Depends(get_db)) -> LoginResponse:
    """Authentifie un utilisateur et renvoie un jeton de session."""
    result = auth_service.authenticate(
        db,
        payload.email,
        payload.password,
        ip_address=client_ip(request),
        user_agent=user_agent(request),
        lang=request_language(request),
    )

    if result.status == "locked":
        raise HTTPException(status_code=status.HTTP_423_LOCKED, detail=result.message)
    if result.status == "invalid":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=result.message)
    if result.status == "inactive":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=result.message)

    assert result.user is not None
    token, expires_in = create_access_token(result.user.id, result.user.email)
    return LoginResponse(access_token=token, expires_in=expires_in, user=UserOut.model_validate(result.user))


@router.get("/me", response_model=UserOut)
def me(user: User = Depends(get_current_user)) -> UserOut:
    """Retourne le profil de l'utilisateur connecté."""
    return UserOut.model_validate(user)


@router.patch("/profile")
def update_profile(
    payload: UpdateProfileRequest,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """
    Met à jour les coordonnées et préférences régionales (pays, ville,
    téléphone international, fuseau, devise d'affichage, locale).
    """
    ok, message = auth_service.update_user_profile(
        db,
        user,
        **payload.model_dump(exclude_none=True),
        ip_address=client_ip(request),
        lang=request_language(request, user),
    )
    if not ok:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=message)
    return {"status": "ok", "message": message, "user": UserOut.model_validate(user)}


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
    from app.services.alerts import log_action

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
    from app.security import hash_pin, validate_pin_format, verify_password
    from app.services.alerts import log_action

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
