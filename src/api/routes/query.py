from fastapi import APIRouter

from src.shared.models import QueryRequest, QueryResult

router = APIRouter(tags=["query"])


@router.post("/query", response_model=QueryResult)
def query(request: QueryRequest) -> QueryResult:
    # TODO: authenticate -> NLP -> schema linking -> IR -> permissions
    # -> planning -> SQL validation -> execution -> formatting.
    return QueryResult(
        columns=["question"],
        rows=[{"question": request.question}],
        row_count=1,
        is_mock=True,
        message="Mock response; NLP and SQL execution are not implemented.",
    )
