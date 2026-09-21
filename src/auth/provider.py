"""Authentication provider abstraction and implementations."""

from abc import ABC, abstractmethod
import secrets
from typing import Callable

from starlette.requests import Request

from src.auth.models import (
    AuthenticatedIdentity,
    LoginRequest,
    RefreshRequest,
    TokenResponse,
)
from src.auth.tokens import (
    create_access_token,
    create_refresh_token,
    decode_access_token,
    decode_refresh_token,
)
from src.shared.exceptions import AuthenticationError

# Default seed / catalog users for development and local testing
DEFAULT_USERS: dict[str, tuple[str, AuthenticatedIdentity]] = {
    "admin": (
        "admin123",
        AuthenticatedIdentity(user_id="admin", role="admin", tenant_id="1"),
    ),
    "user0001": (
        "Passw0rd!",
        AuthenticatedIdentity(
            user_id="user0001",
            role="viewer",
            tenant_id="1",
            employee_id="1",
            department_id="1",
            branch_id="1",
            team_id="1",
        ),
    ),
    "hr_mgr": (
        "Passw0rd!",
        AuthenticatedIdentity(
            user_id="hr_mgr",
            role="department_manager",
            tenant_id="T1",
            department_id="D2",
        ),
    ),
    "sales_mgr": (
        "Passw0rd!",
        AuthenticatedIdentity(
            user_id="sales_mgr",
            role="department_manager",
            tenant_id="T1",
            department_id="D1",
        ),
    ),
    "demo": (
        "demo123",
        AuthenticatedIdentity(user_id="demo", role="viewer", tenant_id="T1"),
    ),
}


class AuthenticationProvider(ABC):
    """Abstract authentication provider verifying request credentials and producing trusted identity."""

    @abstractmethod
    def authenticate(self, request: Request) -> AuthenticatedIdentity:
        """Verify caller credentials and extract AuthenticatedIdentity.

        Raises:
            AuthenticationError: If identity cannot be verified or credentials are missing/invalid.
        """
        raise NotImplementedError

    @abstractmethod
    def login(self, request: LoginRequest) -> TokenResponse:
        """Authenticate user credentials and issue access and refresh tokens.

        Raises:
            AuthenticationError: If credentials are invalid.
        """
        raise NotImplementedError

    @abstractmethod
    def refresh(self, request: RefreshRequest) -> TokenResponse:
        """Validate refresh token and issue a new access token.

        Raises:
            AuthenticationError: If refresh token is invalid or expired.
        """
        raise NotImplementedError


class DefaultAuthenticationProvider(AuthenticationProvider):
    """Production and default authentication provider.

    Supports JWT Bearer token authentication, credential login, and token refresh.
    Fails closed when unauthenticated or when credentials cannot be verified.
    """

    def __init__(
        self,
        users: dict[str, tuple[str, AuthenticatedIdentity]] | None = None,
    ) -> None:
        self._users = users if users is not None else DEFAULT_USERS

    def authenticate(self, request: Request) -> AuthenticatedIdentity:
        auth_header = request.headers.get("Authorization")
        if not auth_header:
            raise AuthenticationError("Authentication provider is required for protected endpoints.")

        parts = auth_header.strip().split()
        if len(parts) != 2 or parts[0].lower() != "bearer":
            raise AuthenticationError("Invalid Authorization header format; Bearer token required.")

        token = parts[1]
        return decode_access_token(token)

    def login(self, request: LoginRequest) -> TokenResponse:
        user_record = self._users.get(request.username)
        if user_record is None:
            raise AuthenticationError("Invalid username or password.")

        expected_password, identity = user_record
        if not secrets.compare_digest(request.password, expected_password):
            raise AuthenticationError("Invalid username or password.")

        access_token, expires_in = create_access_token(identity)
        refresh_token = create_refresh_token(identity)

        return TokenResponse(
            access_token=access_token,
            token_type="bearer",
            expires_in=expires_in,
            refresh_token=refresh_token,
        )

    def refresh(self, request: RefreshRequest) -> TokenResponse:
        identity = decode_refresh_token(request.refresh_token)
        access_token, expires_in = create_access_token(identity)
        new_refresh_token = create_refresh_token(identity)

        return TokenResponse(
            access_token=access_token,
            token_type="bearer",
            expires_in=expires_in,
            refresh_token=new_refresh_token,
        )


class MockAuthenticationProvider(AuthenticationProvider):
    """Deterministic test provider used to inject trusted identities during tests."""

    def __init__(
        self,
        identity: AuthenticatedIdentity | Callable[[Request], AuthenticatedIdentity] | None = None,
        credentials: dict[str, tuple[str, AuthenticatedIdentity]] | None = None,
    ) -> None:
        self._identity = identity
        self._credentials = credentials or {}

    def authenticate(self, request: Request) -> AuthenticatedIdentity:
        if self._identity is None:
            # Fall back to Bearer token decoding if available
            auth_header = request.headers.get("Authorization")
            if auth_header and auth_header.startswith("Bearer "):
                return decode_access_token(auth_header.split(" ", 1)[1])
            raise AuthenticationError("No authenticated identity provided.")
        if callable(self._identity):
            return self._identity(request)
        return self._identity

    def login(self, request: LoginRequest) -> TokenResponse:
        if request.username in self._credentials:
            expected_pass, ident = self._credentials[request.username]
            if secrets.compare_digest(request.password, expected_pass):
                access_token, expires_in = create_access_token(ident)
                refresh_token = create_refresh_token(ident)
                return TokenResponse(
                    access_token=access_token,
                    token_type="bearer",
                    expires_in=expires_in,
                    refresh_token=refresh_token,
                )
            raise AuthenticationError("Invalid mock credentials.")

        if isinstance(self._identity, AuthenticatedIdentity):
            if request.username == self._identity.user_id and request.password == "correct_password":
                access_token, expires_in = create_access_token(self._identity)
                refresh_token = create_refresh_token(self._identity)
                return TokenResponse(
                    access_token=access_token,
                    token_type="bearer",
                    expires_in=expires_in,
                    refresh_token=refresh_token,
                )

        raise AuthenticationError("Invalid mock credentials.")

    def refresh(self, request: RefreshRequest) -> TokenResponse:
        identity = decode_refresh_token(request.refresh_token)
        access_token, expires_in = create_access_token(identity)
        new_refresh_token = create_refresh_token(identity)
        return TokenResponse(
            access_token=access_token,
            token_type="bearer",
            expires_in=expires_in,
            refresh_token=new_refresh_token,
        )
