from copy import deepcopy
from datetime import date, datetime, timezone

import pytest
from sqlalchemy import create_engine

from src.query.handlers.complex import ComplexQueryHandler
from src.query.ir import PeriodComparison
from src.shared.exceptions import InvalidColumnError, MalformedQueryIRError


def comparison():
    return dict(
        query=dict(intent='SUM', tables=['orders'], select=['total_amount'], filters=[
            dict(column='customer_id', value=1, source='permission'),
            dict(column='status', value='PAID'),
        ]),
        date_column='orders.order_date',
        periods=[dict(label='previous', start=date(2026, 8, 1), end=date(2026, 9, 1)),
                 dict(label='current', start=date(2026, 9, 1), end=date(2026, 10, 1))],
    )


def test_comparison_boundaries_and_permission_filters_execute(registry):
    request = PeriodComparison.model_validate(comparison())
    before = request.model_dump()
    results = ComplexQueryHandler(registry).compare(request)
    assert request.model_dump() == before
    assert [r.period.label for r in results] == ['previous', 'current']
    for result in results:
        assert 'LIMIT' not in result.query.sql
        assert '"order_date" >= :p2' in result.query.sql
        assert '"order_date" < :p3' in result.query.sql
        assert result.query.params == {'p0': 1, 'p1': 'PAID', 'p2': result.period.start, 'p3': result.period.end}
    engine = create_engine('sqlite://')
    try:
        with engine.begin() as conn:
            conn.exec_driver_sql("ATTACH DATABASE ':memory:' AS public")
            conn.exec_driver_sql('CREATE TABLE public.orders (customer_id INTEGER, total_amount NUMERIC, order_date DATE, status TEXT)')
            conn.exec_driver_sql("INSERT INTO public.orders VALUES (1,10,'2026-08-01','PAID'), (1,20,'2026-08-31','PAID'), (1,50,'2026-09-01','PAID'), (1,90,'2026-10-01','PAID'), (2,1000,'2026-08-15','PAID'), (1,2000,'2026-09-15','NEW'), (1,3000,'2026-07-31','PAID')")
            assert [conn.execute(r.query.statement).scalar_one() for r in results] == [30, 50]
    finally:
        engine.dispose()


@pytest.mark.parametrize('periods', [
    [], [dict(label='only', start='2026-08-01', end='2026-09-01')],
    [dict(label='same', start='2026-08-01', end='2026-09-01')] * 2,
    [dict(label='a', start='2026-09-01', end='2026-08-01'), dict(label='b', start='2026-09-01', end='2026-10-01')],
    [dict(label='a', start='2026-09-01', end='2026-09-01'), dict(label='b', start='2026-09-01', end='2026-10-01')],
    [dict(label=' ', start='2026-08-01', end='2026-09-01'), dict(label='b', start='2026-09-01', end='2026-10-01')],
    [dict(label='a', start=date(2026, 8, 1), end=datetime(2026, 9, 1)), dict(label='b', start='2026-09-01', end='2026-10-01')],
    [dict(label='a', start=datetime(2026, 8, 1), end=datetime(2026, 9, 1, tzinfo=timezone.utc)), dict(label='b', start='2026-09-01', end='2026-10-01')],
])
def test_invalid_periods(registry, periods):
    request = comparison()
    request['periods'] = periods
    with pytest.raises(MalformedQueryIRError):
        ComplexQueryHandler(registry).compare(request)


@pytest.mark.parametrize('column,error', [('orders.status', MalformedQueryIRError), ('orders.missing', InvalidColumnError)])
def test_invalid_period_column(registry, column, error):
    request = comparison()
    request['date_column'] = column
    with pytest.raises(error):
        ComplexQueryHandler(registry).compare(request)


@pytest.mark.parametrize('changes', [
    dict(intent='LOOKUP', select=['id']),
    dict(group_by=['customer_id']),
    dict(having=[dict(column='*', aggregation='COUNT', value=1)]),
    dict(order_by=[dict(column='total_amount')]),
])
def test_comparison_rejects_unsupported_shapes(registry, changes):
    request = comparison()
    request['query'].update(changes)
    with pytest.raises(MalformedQueryIRError):
        ComplexQueryHandler(registry).compare(request)


def test_comparison_revalidates_mutated_models(registry):
    request = PeriodComparison.model_validate(comparison())
    request.periods[0].end = request.periods[0].start
    with pytest.raises(MalformedQueryIRError):
        ComplexQueryHandler(registry).compare(request)


def test_comparison_dictionary_is_not_mutated(registry):
    request = comparison()
    before = deepcopy(request)
    assert len(ComplexQueryHandler(registry).compare(request)) == 2
    assert request == before
