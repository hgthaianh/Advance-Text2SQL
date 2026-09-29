from fastapi import APIRouter

from src.api.dependencies import AuthenticatedUser
from src.api.errors import error_response
from src.pipeline.orchestrator import Text2SQLPipeline
from src.shared.models import ErrorResponse, QueryRequest, QueryResponse, QueryResult

router = APIRouter(tags=["query"])
pipeline = Text2SQLPipeline()


@router.post("/query", response_model=QueryResponse, responses={
    403: {'model': ErrorResponse}, 422: {'model': ErrorResponse}, 503: {'model': ErrorResponse},
})
def query(request: QueryRequest, user: AuthenticatedUser) -> QueryResult:
    result = pipeline.execute(request.question, user)
    return result if result.success else error_response(result)
