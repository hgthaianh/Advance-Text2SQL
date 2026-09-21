"""Authentication providers for trusted identities and token sessions."""

from abc import ABC, abstractmethod
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import secrets
from typing import Callable

from sqlalchemy import Engine, text
from starlette.requests import Request

from src.auth.models import AuthenticatedIdentity, LoginRequest, LogoutRequest, RefreshRequest, TokenResponse
from src.auth.tokens import create_access_token, create_refresh_token, decode_access_token, decode_refresh_token, revoke_token
from src.config import get_settings
from src.database.session import get_engine
from src.shared.exceptions import AuthenticationError


class AuthenticationProvider(ABC):
    """Verify credentials and issue tokens without trusting client identity headers."""

    @abstractmethod
    def authenticate(self, request: Request) -> AuthenticatedIdentity:
        raise NotImplementedError

    @abstractmethod
    def login(self, payload: LoginRequest) -> TokenResponse:
        raise NotImplementedError

    @abstractmethod
    def refresh(self, payload: RefreshRequest) -> TokenResponse:
        raise NotImplementedError

    @abstractmethod
    def logout(self, identity: AuthenticatedIdentity, payload: LogoutRequest) -> None:
        raise NotImplementedError


_USER_BY_USERNAME = text("""
    SELECT u.id, u.tenant_id, u.branch_id, u.department_id, u.team_id, u.employee_id, r.code AS role_code
    FROM iam.users u
    JOIN iam.user_roles ur ON ur.user_id = u.id AND (ur.valid_to IS NULL OR ur.valid_to > CURRENT_TIMESTAMP)
    JOIN iam.roles r ON r.id = ur.role_id AND r.is_active = TRUE
    WHERE u.username = :username AND u.status = 'ACTIVE'
      AND u.password_hash IS NOT NULL AND u.password_hash = crypt(:password, u.password_hash)
    ORDER BY r.id LIMIT 1
""")
_USER_BY_ID = text("""
    SELECT u.id, u.tenant_id, u.branch_id, u.department_id, u.team_id, u.employee_id, r.code AS role_code
    FROM iam.users u
    JOIN iam.user_roles ur ON ur.user_id = u.id AND (ur.valid_to IS NULL OR ur.valid_to > CURRENT_TIMESTAMP)
    JOIN iam.roles r ON r.id = ur.role_id AND r.is_active = TRUE
    WHERE u.id = :user_id AND u.status = 'ACTIVE'
    ORDER BY r.id LIMIT 1
""")
_INSERT_SESSION = text("""
    INSERT INTO iam.login_sessions (user_id, session_token_hash, user_agent, expires_at)
    VALUES (:user_id, :token_hash, :user_agent, :expires_at)
""")
_CONSUME_SESSION = text("""
    UPDATE iam.login_sessions SET revoked_at = CURRENT_TIMESTAMP
    WHERE session_token_hash = :token_hash AND revoked_at IS NULL AND expires_at > CURRENT_TIMESTAMP
    RETURNING user_id
""")
_REVOKE_SESSION = text("""
    UPDATE iam.login_sessions SET revoked_at = CURRENT_TIMESTAMP
    WHERE user_id = :user_id AND session_token_hash = :token_hash AND revoked_at IS NULL
""")


def _token_hash(token: str) -> str:
    return sha256(token.encode()).hexdigest()


def _role_from_code(code: str) -> str:
    """Map the seeded IAM role catalogue to deterministic permission roles."""
    prefix = code.split('_', 1)[0].lower()
    if prefix == 'admin':
        return 'admin'
    if prefix in {'hr', 'finance', 'sales', 'support'}:
        return 'department_manager'
    return 'employee'


class DefaultAuthenticationProvider(AuthenticationProvider):
    """PostgreSQL-backed provider using IAM users, roles and login sessions."""

    def __init__(self, engine: Engine | None = None) -> None:
        self.engine = engine or get_engine()

    def authenticate(self, request: Request) -> AuthenticatedIdentity:
        parts = request.headers.get('Authorization', '').strip().split()
        if len(parts) != 2 or parts[0].lower() != 'bearer':
            raise AuthenticationError('Bearer authentication is required')
        return decode_access_token(parts[1])

    def _identity(self, row: dict) -> AuthenticatedIdentity:
        return AuthenticatedIdentity(
            user_id=str(row['id']), role=_role_from_code(str(row['role_code'])), tenant_id=str(row['tenant_id']),
            employee_id=str(row['employee_id']) if row['employee_id'] is not None else None,
            department_id=str(row['department_id']) if row['department_id'] is not None else None,
            branch_id=str(row['branch_id']) if row['branch_id'] is not None else None,
            team_id=str(row['team_id']) if row['team_id'] is not None else None,
        )

    def _issue(self, connection, row: dict) -> TokenResponse:
        identity = self._identity(row)
        access_token, expires_in = create_access_token(identity)
        refresh_token = create_refresh_token(identity)
        expires_at = datetime.now(timezone.utc) + timedelta(minutes=get_settings().auth_refresh_token_expire_minutes)
        connection.execute(_INSERT_SESSION, {
            'user_id': row['id'], 'token_hash': _token_hash(refresh_token), 'user_agent': None, 'expires_at': expires_at,
        })
        return TokenResponse(access_token=access_token, expires_in=expires_in, refresh_token=refresh_token)

    def login(self, payload: LoginRequest) -> TokenResponse:
        with self.engine.begin() as connection:
            row = connection.execute(_USER_BY_USERNAME, {'username': payload.username, 'password': payload.password}).mappings().first()
            if row is None:
                raise AuthenticationError('Invalid credentials')
            return self._issue(connection, dict(row))

    def refresh(self, payload: RefreshRequest) -> TokenResponse:
        decode_refresh_token(payload.refresh_token)
        with self.engine.begin() as connection:
            user_id = connection.execute(_CONSUME_SESSION, {'token_hash': _token_hash(payload.refresh_token)}).scalar_one_or_none()
            if user_id is None:
                raise AuthenticationError('Invalid refresh token')
            row = connection.execute(_USER_BY_ID, {'user_id': user_id}).mappings().first()
            if row is None:
                raise AuthenticationError('Inactive user')
            return self._issue(connection, dict(row))

    def logout(self, identity: AuthenticatedIdentity, payload: LogoutRequest) -> None:
        refresh_identity = decode_refresh_token(payload.refresh_token)
        if refresh_identity.user_id != identity.user_id:
            raise AuthenticationError('Refresh token does not belong to authenticated user')
        with self.engine.begin() as connection:
            connection.execute(_REVOKE_SESSION, {'user_id': identity.user_id, 'token_hash': _token_hash(payload.refresh_token)})


class MockAuthenticationProvider(AuthenticationProvider):
    """Deterministic in-memory provider used only by tests."""

    def __init__(self, identity: AuthenticatedIdentity | Callable[[Request], AuthenticatedIdentity] | None = None,
                 credentials: dict[str, tuple[str, AuthenticatedIdentity]] | None = None) -> None:
        self._identity = identity
        self._credentials = credentials or {}

    def authenticate(self, request: Request) -> AuthenticatedIdentity:
        if self._identity is None:
            header = request.headers.get('Authorization', '')
            if header.startswith('Bearer '):
                return decode_access_token(header[7:])
            raise AuthenticationError('No authenticated identity provided')
        return self._identity(request) if callable(self._identity) else self._identity

    def login(self, payload: LoginRequest) -> TokenResponse:
        record = self._credentials.get(payload.username)
        if record is None or not secrets.compare_digest(payload.password, record[0]):
            raise AuthenticationError('Invalid credentials')
        access_token, expires_in = create_access_token(record[1])
        return TokenResponse(access_token=access_token, expires_in=expires_in, refresh_token=create_refresh_token(record[1]))

    def refresh(self, payload: RefreshRequest) -> TokenResponse:
        identity = decode_refresh_token(payload.refresh_token)
        revoke_token(payload.refresh_token)
        access_token, expires_in = create_access_token(identity)
        return TokenResponse(access_token=access_token, expires_in=expires_in, refresh_token=create_refresh_token(identity))

    def logout(self, identity: AuthenticatedIdentity, payload: LogoutRequest) -> None:
        refresh_identity = decode_refresh_token(payload.refresh_token)
        if refresh_identity.user_id != identity.user_id:
            raise AuthenticationError('Refresh token does not belong to authenticated user')
        revoke_token(payload.refresh_token)
