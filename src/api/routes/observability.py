"""Authenticated observability output."""

from fastapi import APIRouter, HTTPException, Query

from src.api.dependencies import ObservabilityAdmin
from src.database.session import get_engine
from src.pipeline.observability import PersistedPipelineObserver


router = APIRouter(tags=['observability'])


@router.get('/observability/summary')
def observability_summary(_: ObservabilityAdmin) -> dict:
    """Expose no request text, identity, SQL, bind values, or exception detail."""
    return PersistedPipelineObserver(get_engine()).aggregate()


@router.get('/observability/history')
def observability_history(
    _: ObservabilityAdmin,
    query_id: str | None = Query(default=None, min_length=1, max_length=64),
    limit: int = Query(default=100, ge=1, le=100),
) -> list[dict]:
    """List retained admin trace records, optionally for one query ID."""
    return PersistedPipelineObserver(get_engine()).history(query_id=query_id, limit=limit)


@router.get('/observability/history/{query_id}')
def observability_history_detail(query_id: str, _: ObservabilityAdmin) -> dict:
    """Return one admin trace, including its prompt but no identity or parameters."""
    records = PersistedPipelineObserver(get_engine()).history(query_id=query_id, limit=1)
    if not records:
        raise HTTPException(status_code=404, detail={
            'error_code': 'QUERY_HISTORY_NOT_FOUND',
            'message': 'No retained query history matches this query ID.',
        })
    return records[0]
