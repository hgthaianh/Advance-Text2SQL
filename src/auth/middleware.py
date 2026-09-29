"""Authentication boundary middleware."""

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette import status

from src.auth.models import AuthenticatedIdentity
from src.auth.provider import AuthenticationProvider, DefaultAuthenticationProvider
from src.shared.exceptions import AuthenticationError


PUBLIC_PATHS = frozenset({
    "/health",
    "/health/ready",
    "/openapi.json",
    "/docs",
    "/redoc",
    "/auth/login",
    "/auth/refresh",
})


class AuthenticationMiddleware(BaseHTTPMiddleware):
    """Intercept protected requests, authenticate the caller, and establish trusted identity in request.state."""

    def __init__(self, app, provider: AuthenticationProvider | None = None) -> None:
        super().__init__(app)
        self.default_provider = provider or DefaultAuthenticationProvider()

    def _resolve_provider(self, request: Request) -> AuthenticationProvider:
        from src.api.dependencies import get_authentication_provider

        # Check for test dependency overrides first
        app = request.app
        if hasattr(app, "dependency_overrides") and get_authentication_provider in app.dependency_overrides:
            override = app.dependency_overrides[get_authentication_provider]
            return override() if callable(override) else override

        return getattr(app.state, "auth_provider", None) or self.default_provider

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        if request.url.path in PUBLIC_PATHS:
            return await call_next(request)

        provider = self._resolve_provider(request)
        try:
            identity = provider.authenticate(request)
            if not isinstance(identity, AuthenticatedIdentity):
                return JSONResponse(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    content={
                        "query_id": None,
                        "error_code": "AUTHENTICATION_REQUIRED",
                        "message": "Authentication is required.",
                    },
                )
            request.state.identity = identity
        except AuthenticationError:
            return JSONResponse(
                status_code=status.HTTP_401_UNAUTHORIZED,
                content={
                    "query_id": None,
                    "error_code": "AUTHENTICATION_REQUIRED",
                    "message": "Authentication is required.",
                },
            )

        return await call_next(request)
