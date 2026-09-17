import pytest
from pydantic import ValidationError
from src.query.ir import QueryIR
from src.shared.enums import Intent


def test_query_ir_validates():
    query = QueryIR.model_validate({
        "intent": "select", "tables": ["employees"], "select": ["name"],
        "filters": [{"column": "branch_id", "operator": "eq", "value": "hn"}],
        "group_by": [], "order_by": [{"column": "name", "direction": "asc"}],
        "limit": 10,
    })
    assert query.intent == Intent.SELECT
    assert query.limit == 10


def test_query_ir_rejects_invalid_limit():
    with pytest.raises(ValidationError):
        QueryIR(limit=0)
