from fastapi import APIRouter, status
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from src.api.errors import service_unavailable_response
from src.database.session import get_engine

router = APIRouter(tags=["health"])


@router.get("/health")
def health() -> dict[str, str]:
    """Process liveness only; does not probe PostgreSQL."""
    return {"status": "ok"}


@router.get(
    "/health/ready",
    response_model=None,
    responses={status.HTTP_503_SERVICE_UNAVAILABLE: {"description": "PostgreSQL unavailable"}},
)
def readiness() -> dict[str, str] | JSONResponse:
    """Report readiness only when the PostgreSQL connection can serve a query."""
    try:
        with get_engine().connect() as connection:
            connection.execute(text("SELECT 1"))
    except SQLAlchemyError:
        return service_unavailable_response()
    return {"status": "ok"}
