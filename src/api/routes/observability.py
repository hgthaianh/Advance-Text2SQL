"""Authenticated, aggregate-only observability output."""

from fastapi import APIRouter

from src.api.dependencies import ObservabilityAdmin
from src.database.session import get_engine
from src.pipeline.observability import PersistedPipelineObserver


router = APIRouter(tags=['observability'])


@router.get('/observability/summary')
def observability_summary(_: ObservabilityAdmin) -> dict:
    """Expose no request text, identity, SQL, bind values, or exception detail."""
    return PersistedPipelineObserver(get_engine()).aggregate()
