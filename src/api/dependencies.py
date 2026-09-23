from typing import Annotated

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from src.auth.models import AuthenticatedIdentity
from src.auth.provider import AuthenticationProvider, DefaultAuthenticationProvider
from src.config import Settings, get_settings
from src.database.session import get_session
from src.shared.models import UserContext

SettingsDependency = Annotated[Settings, Depends(get_settings)]
SessionDependency = Annotated[Session, Depends(get_session)]


def get_authentication_provider() -> AuthenticationProvider:
    """Resolve the active AuthenticationProvider."""
    return DefaultAuthenticationProvider()


def get_authenticated_user(request: Request) -> UserContext:
    """Build UserContext strictly from verified request.state.identity.

    If the request has not crossed a verified authentication boundary,
    this raises HTTP 401 AUTHENTICATION_REQUIRED.
    Unauthenticated caller-supplied headers can never become a UserContext.
    """
    identity = getattr(request.state, "identity", None)
    if not isinstance(identity, AuthenticatedIdentity):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={
                "error_code": "AUTHENTICATION_REQUIRED",
                "message": "Authenticated user, role, and tenant identity are required.",
            },
        )
    return identity.to_user_context()


AuthenticatedUser = Annotated[UserContext, Depends(get_authenticated_user)]


def require_observability_admin(user: AuthenticatedUser) -> UserContext:
    """Allow global operational metrics only to trusted administrative roles."""
    if user.role.lower() not in {'admin', 'superuser'}:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                'error_code': 'PERMISSION_DENIED',
                'message': 'Observability access requires an administrator role.',
            },
        )
    return user


ObservabilityAdmin = Annotated[UserContext, Depends(require_observability_admin)]
