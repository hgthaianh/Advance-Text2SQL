from evaluation.run_erp_qa_regression import _filter_evidence


def test_filter_evidence_understands_compiled_postgresql_identifiers():
    sql = (
        'SELECT * FROM "sales"."orders" '
        'WHERE "sales"."orders"."order_no" = :p0 '
        'AND "sales"."orders"."order_date" >= :p1'
    )

    assert _filter_evidence(
        ['order_code=SO000123', 'po_date between 2026-01-01 and 2026-09-16'],
        sql,
        {'p0': 'SO000123', 'p1': '2026-01-01', 'p2': '2026-09-17'},
    ) == (True, True)
