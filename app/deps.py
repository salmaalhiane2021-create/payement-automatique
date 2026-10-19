"""
Dépendances FastAPI partagées : session de base de données, utilisateur
courant (via JWT), contrôle de l'état du compte.
"""

from __future__ import annotations

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import User
from app.security import decode_access_token

# auto_error=False : on renvoie nous-mêmes une erreur en français
bearer_scheme = HTTPBearer(auto_error=False)


def client_ip(request: Request) -> str:
    """Adresse IP du client (derrière un proxy, X-Forwarded-For fait foi)."""
    forwarded = request.headers.get("x-forwarded-for", "")
    if forwarded:
        return forwarded.split(",")[0].strip()[:45]
    return (request.client.host if request.client else "")[:45]


def user_agent(request: Request) -> str:
    """User-Agent tronqué (journalisation d'audit)."""
    return request.headers.get("user-agent", "")[:200]


def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    db: Session = Depends(get_db),
) -> User:
    """Résout l'utilisateur à partir du jeton Bearer ; 401 sinon."""
    if credentials is None or not credentials.credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentification requise.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    payload = decode_access_token(credentials.credentials)
    if not payload:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Session expirée ou jeton invalide.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    user = db.get(User, int(payload.get("sub", 0)))
    if user is None or not user.is_active:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Compte introuvable ou désactivé.")
    return user
