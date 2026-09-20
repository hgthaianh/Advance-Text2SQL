from fastapi import APIRouter

from src.pipeline.orchestrator import Text2SQLPipeline
from src.shared.models import QueryRequest, QueryResult

router = APIRouter(tags=["query"])
pipeline = Text2SQLPipeline()


@router.post("/query", response_model=QueryResult)
def query(request: QueryRequest) -> QueryResult:
    return pipeline.execute(request.question, request.user)
