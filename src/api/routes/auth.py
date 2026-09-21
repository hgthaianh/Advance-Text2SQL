"""Authentication API endpoints."""

from typing import Annotated

from fastapi import APIRouter, Depends, Request, status
from fastapi.responses import JSONResponse, Response

from src.api.dependencies import get_authentication_provider
from src.auth.models import (
    AuthenticatedIdentity,
    IdentityResponse,
    LoginRequest,
    LogoutRequest,
    RefreshRequest,
    TokenResponse,
)
from src.auth.provider import AuthenticationProvider
from src.shared.exceptions import AuthenticationError
from src.shared.models import ErrorResponse

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post(
    "/login",
    response_model=TokenResponse,
    status_code=status.HTTP_200_OK,
    responses={
        status.HTTP_401_UNAUTHORIZED: {
            "model": ErrorResponse,
            "description": "Invalid username or password",
        },
    },
    summary="Authenticate user credentials",
    description="Authenticates the caller with username and password, returning an access token and refresh token.",
)
def login(
    payload: LoginRequest,
    provider: Annotated[AuthenticationProvider, Depends(get_authentication_provider)],
) -> TokenResponse | JSONResponse:
    try:
        return provider.login(payload)
    except AuthenticationError:
        return JSONResponse(
            status_code=status.HTTP_401_UNAUTHORIZED,
            content={
                "query_id": None,
                "error_code": "INVALID_CREDENTIALS",
                "message": "Invalid username or password.",
            },
        )


@router.post(
    "/refresh",
    response_model=TokenResponse,
    status_code=status.HTTP_200_OK,
    responses={
        status.HTTP_401_UNAUTHORIZED: {
            "model": ErrorResponse,
            "description": "Invalid, expired, or revoked refresh token",
        },
    },
    summary="Refresh access token",
    description="Validates a refresh token and issues a new access token without changing user identity.",
)
def refresh(
    payload: RefreshRequest,
    provider: Annotated[AuthenticationProvider, Depends(get_authentication_provider)],
) -> TokenResponse | JSONResponse:
    try:
        return provider.refresh(payload)
    except AuthenticationError:
        return JSONResponse(
            status_code=status.HTTP_401_UNAUTHORIZED,
            content={
                "query_id": None,
                "error_code": "INVALID_REFRESH_TOKEN",
                "message": "Invalid, expired, or revoked refresh token.",
            },
        )


@router.post(
    "/logout",
    status_code=status.HTTP_204_NO_CONTENT,
    response_model=None,
    responses={status.HTTP_401_UNAUTHORIZED: {"model": ErrorResponse}},
    summary="Revoke the current refresh-token session",
)
def logout(
    payload: LogoutRequest,
    request: Request,
    provider: Annotated[AuthenticationProvider, Depends(get_authentication_provider)],
) -> Response | JSONResponse:
    identity = getattr(request.state, "identity", None)
    if not isinstance(identity, AuthenticatedIdentity):
        return JSONResponse(status_code=status.HTTP_401_UNAUTHORIZED, content={
            "query_id": None, "error_code": "AUTHENTICATION_REQUIRED", "message": "Authentication is required.",
        })
    try:
        provider.logout(identity, payload)
    except AuthenticationError:
        return JSONResponse(status_code=status.HTTP_401_UNAUTHORIZED, content={
            "query_id": None, "error_code": "INVALID_REFRESH_TOKEN", "message": "Invalid refresh token.",
        })
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get(
    "/me",
    response_model=IdentityResponse,
    status_code=status.HTTP_200_OK,
    responses={
        status.HTTP_401_UNAUTHORIZED: {
            "model": ErrorResponse,
            "description": "Authentication is required",
        },
    },
    summary="Get current authenticated identity",
    description="Returns the trusted caller identity established by the authentication provider.",
)
def get_me(request: Request) -> IdentityResponse | JSONResponse:
    identity = getattr(request.state, "identity", None)
    if not isinstance(identity, AuthenticatedIdentity):
        return JSONResponse(
            status_code=status.HTTP_401_UNAUTHORIZED,
            content={
                "query_id": None,
                "error_code": "AUTHENTICATION_REQUIRED",
                "message": "Authentication is required.",
            },
        )

    return IdentityResponse(
        user_id=identity.user_id,
        role=identity.role,
        tenant_id=identity.tenant_id,
        employee_id=identity.employee_id,
        department_id=identity.department_id,
        branch_id=identity.branch_id,
        team_id=identity.team_id,
    )
