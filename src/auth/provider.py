"""Authentication provider abstraction and implementations."""

from abc import ABC, abstractmethod
from typing import Callable

from starlette.requests import Request

from src.auth.models import AuthenticatedIdentity
from src.shared.exceptions import AuthenticationError


class AuthenticationProvider(ABC):
    """Abstract authentication provider verifying request credentials and producing trusted identity."""

    @abstractmethod
    def authenticate(self, request: Request) -> AuthenticatedIdentity:
        """Verify caller credentials and extract AuthenticatedIdentity.

        Raises:
            AuthenticationError: If identity cannot be verified or credentials are missing/invalid.
        """
        raise NotImplementedError


class DefaultAuthenticationProvider(AuthenticationProvider):
    """Production authentication provider placeholder.

    Fails closed until the team connects the finalized external authentication provider
    (e.g., API Gateway, OAuth2, JWT, enterprise SSO).
    """

    def authenticate(self, request: Request) -> AuthenticatedIdentity:
        # Production fails closed: unauthenticated requests cannot proceed
        raise AuthenticationError("Authentication provider is required for protected endpoints.")


class MockAuthenticationProvider(AuthenticationProvider):
    """Deterministic test provider used to inject trusted identities during tests."""

    def __init__(self, identity: AuthenticatedIdentity | Callable[[Request], AuthenticatedIdentity] | None = None) -> None:
        self._identity = identity

    def authenticate(self, request: Request) -> AuthenticatedIdentity:
        if self._identity is None:
            raise AuthenticationError("No authenticated identity provided.")
        if callable(self._identity):
            return self._identity(request)
        return self._identity
