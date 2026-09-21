"""Stable translation from controlled pipeline failures to HTTP responses."""

from fastapi import status
from fastapi.responses import JSONResponse

from src.shared.models import ErrorResponse, QueryResult


_STATUS_BY_ERROR_CODE = {
    'PERMISSION_DENIED': status.HTTP_403_FORBIDDEN,
    'SQL_EXECUTION_ERROR': status.HTTP_503_SERVICE_UNAVAILABLE,
}
_UNPROCESSABLE_CODES = {
    'AMBIGUOUS_QUERY', 'MISSING_ENTITY', 'UNSUPPORTED_QUERY',
    'INVALID_QUERY_IR', 'SQL_VALIDATION_ERROR',
}


def error_response(result: QueryResult) -> JSONResponse:
    """Return only public error data for a failed pipeline result."""
    if result.success or not result.error_code:
        raise ValueError('error_response requires a failed result with an error code')
    status_code = _STATUS_BY_ERROR_CODE.get(result.error_code)
    if status_code is None:
        status_code = status.HTTP_422_UNPROCESSABLE_CONTENT if result.error_code in _UNPROCESSABLE_CODES else status.HTTP_503_SERVICE_UNAVAILABLE
    body = ErrorResponse(
        query_id=result.query_id,
        error_code=result.error_code,
        message=result.message or 'The query could not be completed.',
    )
    return JSONResponse(status_code=status_code, content=body.model_dump())
