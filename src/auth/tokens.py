"""JWT token operations and token lifecycle management."""

from datetime import datetime, timezone, timedelta
import uuid
from typing import Any

from jose import ExpiredSignatureError, JWTError, jwt

from src.auth.models import AuthenticatedIdentity
from src.config import get_settings
from src.shared.exceptions import AuthenticationError

# In-memory revocation registry for revoked token IDs (jti)
_REVOKED_JTIS: set[str] = set()


def revoke_token(token: str) -> None:
    """Revoke a token by marking its jti as revoked."""
    try:
        settings = get_settings()
        payload = jwt.decode(
            token,
            settings.auth_secret_key.get_secret_value(),
            algorithms=[settings.auth_algorithm],
            options={"verify_exp": False},
        )
        jti = payload.get("jti")
        if jti:
            _REVOKED_JTIS.add(str(jti))
    except JWTError:
        pass


def is_token_revoked(jti: str | None) -> bool:
    """Check if a token ID has been revoked."""
    if not jti:
        return False
    return str(jti) in _REVOKED_JTIS


def clear_revoked_tokens() -> None:
    """Reset revoked tokens (primarily for test cleanup)."""
    _REVOKED_JTIS.clear()


def create_access_token(
    identity: AuthenticatedIdentity,
    expires_delta: timedelta | None = None,
) -> tuple[str, int]:
    """Issue a signed JWT access token for an authenticated identity.

    Returns:
        tuple[str, int]: The encoded JWT string and lifetime in seconds.
    """
    settings = get_settings()
    now = datetime.now(timezone.utc)
    if expires_delta is not None:
        expire_seconds = int(expires_delta.total_seconds())
    else:
        expire_seconds = settings.auth_access_token_expire_minutes * 60

    payload: dict[str, Any] = {
        "sub": identity.user_id,
        "role": identity.role,
        "tenant_id": identity.tenant_id,
        "employee_id": identity.employee_id,
        "department_id": identity.department_id,
        "branch_id": identity.branch_id,
        "team_id": identity.team_id,
        "type": "access",
        "jti": str(uuid.uuid4()),
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(seconds=expire_seconds)).timestamp()),
    }

    token = jwt.encode(
        payload,
        settings.auth_secret_key.get_secret_value(),
        algorithm=settings.auth_algorithm,
    )
    return token, expire_seconds


def create_refresh_token(
    identity: AuthenticatedIdentity,
    expires_delta: timedelta | None = None,
) -> str:
    """Issue a signed JWT refresh token bound to the authenticated identity."""
    settings = get_settings()
    now = datetime.now(timezone.utc)
    if expires_delta is not None:
        expire_seconds = int(expires_delta.total_seconds())
    else:
        expire_seconds = settings.auth_refresh_token_expire_minutes * 60

    payload: dict[str, Any] = {
        "sub": identity.user_id,
        "role": identity.role,
        "tenant_id": identity.tenant_id,
        "employee_id": identity.employee_id,
        "department_id": identity.department_id,
        "branch_id": identity.branch_id,
        "team_id": identity.team_id,
        "type": "refresh",
        "jti": str(uuid.uuid4()),
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(seconds=expire_seconds)).timestamp()),
    }

    return jwt.encode(
        payload,
        settings.auth_secret_key.get_secret_value(),
        algorithm=settings.auth_algorithm,
    )


def decode_access_token(token: str) -> AuthenticatedIdentity:
    """Decode and validate an access token, returning the immutable AuthenticatedIdentity.

    Raises:
        AuthenticationError: If token is invalid, expired, revoked, or not an access token.
    """
    settings = get_settings()
    try:
        payload = jwt.decode(
            token,
            settings.auth_secret_key.get_secret_value(),
            algorithms=[settings.auth_algorithm],
        )
    except ExpiredSignatureError:
        raise AuthenticationError("Access token has expired.") from None
    except JWTError:
        raise AuthenticationError("Invalid access token.") from None

    if payload.get("type") != "access":
        raise AuthenticationError("Invalid token type; access token expected.")

    jti = payload.get("jti")
    if is_token_revoked(jti):
        raise AuthenticationError("Access token has been revoked.")

    user_id = payload.get("sub")
    role = payload.get("role")
    tenant_id = payload.get("tenant_id")
    if not user_id or not role or not tenant_id:
        raise AuthenticationError("Access token missing required identity claims.")

    return AuthenticatedIdentity(
        user_id=str(user_id),
        role=str(role),
        tenant_id=str(tenant_id),
        employee_id=payload.get("employee_id"),
        department_id=payload.get("department_id"),
        branch_id=payload.get("branch_id"),
        team_id=payload.get("team_id"),
    )


def decode_refresh_token(token: str) -> AuthenticatedIdentity:
    """Decode and validate a refresh token, returning the immutable AuthenticatedIdentity.

    Raises:
        AuthenticationError: If token is invalid, expired, revoked, or not a refresh token.
    """
    settings = get_settings()
    try:
        payload = jwt.decode(
            token,
            settings.auth_secret_key.get_secret_value(),
            algorithms=[settings.auth_algorithm],
        )
    except ExpiredSignatureError:
        raise AuthenticationError("Refresh token has expired.") from None
    except JWTError:
        raise AuthenticationError("Invalid refresh token.") from None

    if payload.get("type") != "refresh":
        raise AuthenticationError("Invalid token type; refresh token expected.")

    jti = payload.get("jti")
    if is_token_revoked(jti):
        raise AuthenticationError("Refresh token has been revoked.")

    user_id = payload.get("sub")
    role = payload.get("role")
    tenant_id = payload.get("tenant_id")
    if not user_id or not role or not tenant_id:
        raise AuthenticationError("Refresh token missing required identity claims.")

    return AuthenticatedIdentity(
        user_id=str(user_id),
        role=str(role),
        tenant_id=str(tenant_id),
        employee_id=payload.get("employee_id"),
        department_id=payload.get("department_id"),
        branch_id=payload.get("branch_id"),
        team_id=payload.get("team_id"),
    )
