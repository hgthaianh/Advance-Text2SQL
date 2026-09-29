import pytest

from src.query.handlers.basic import BasicQueryHandler
from src.query.ir import QueryIR
from src.query.models import CompiledQuery
from src.shared.exceptions import UnsupportedIntentError


@pytest.mark.parametrize('intent', ['LOOKUP','FILTER','select','list','detail'])
def test_lookup_compatible_intents(registry,intent):
    result = BasicQueryHandler(registry).handle(QueryIR(intent=intent,tables=['employees'],select=['name']))
    assert isinstance(result, CompiledQuery)
    assert '"employees"."name"' in result.sql


def test_count_with_and_without_permission_filter(registry):
    handler = BasicQueryHandler(registry)
    unfiltered = handler.handle(dict(intent='COUNT',tables=['employees']))
    filtered = handler.handle(dict(intent='COUNT',tables=['employees'],filters=[dict(column='department_id',operator='=',value=12,source='permission')]))
    assert 'WHERE' not in unfiltered.sql
    assert 'WHERE "public"."employees"."department_id" = :p0' in filtered.sql
    assert filtered.params['p0'] == 12
    assert 'count(*)' in filtered.sql


def test_unsupported_intent_does_not_fall_back(registry):
    with pytest.raises(UnsupportedIntentError):
        BasicQueryHandler(registry).handle(dict(intent='comparison',tables=['orders']))


def test_compilation_is_deterministic(registry):
    handler = BasicQueryHandler(registry)
    query = dict(intent='SUM',tables=['orders'],select=['total_amount'],filters=[dict(column='status',operator='=',value='PAID')])
    first,second = handler.handle(query),handler.handle(query)
    assert first.sql == second.sql and first.params == second.params
