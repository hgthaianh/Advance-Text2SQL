from fastapi import APIRouter

from src.api.dependencies import AuthenticatedUser
from src.api.errors import error_response
from src.pipeline.orchestrator import Text2SQLPipeline
from src.shared.models import ErrorResponse, PublicQueryResult, QueryRequest

router = APIRouter(tags=["query"])
pipeline = Text2SQLPipeline()


@router.post("/query", response_model=PublicQueryResult, responses={
    403: {'model': ErrorResponse}, 422: {'model': ErrorResponse}, 503: {'model': ErrorResponse},
})
def query(request: QueryRequest, user: AuthenticatedUser) -> PublicQueryResult:
    if request.previous_question is None:
        result = pipeline.execute(request.question, user)
    else:
        result = pipeline.execute(request.question, user, previous_question=request.previous_question)
    if not result.success:
        return error_response(result)
    # This is a strict backend boundary: no role calling /query receives SQL,
    # bound parameters, execution-plan data, or authorization internals.
    # Admin-only inspection uses the separately authorized observability API.
    return PublicQueryResult(
        success=result.success,
        query_id=result.query_id,
        columns=result.columns,
        rows=result.rows,
        row_count=result.row_count,
        truncated=result.truncated,
        scalar=result.scalar,
        natural_language_response=result.natural_language_response,
    )
