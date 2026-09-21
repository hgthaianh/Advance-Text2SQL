from sqlalchemy import column, create_engine, select, table

from src.database.executor import ReadOnlyExecutor
from src.query.models import CompiledQuery


def test_executor_marks_a_capped_result_as_truncated():
    engine = create_engine('sqlite://')
    records = table('records', column('id'))
    try:
        with engine.begin() as connection:
            connection.exec_driver_sql('CREATE TABLE records (id INTEGER)')
            connection.exec_driver_sql('INSERT INTO records VALUES (1), (2), (3)')
        result = ReadOnlyExecutor(engine, max_rows=2).execute(CompiledQuery(
            sql='SELECT id FROM records', params={}, statement=select(records.c.id).select_from(records),
        ))
    finally:
        engine.dispose()

    assert result.rows == [{'id': 1}, {'id': 2}]
    assert result.row_count == 2
    assert result.truncated is True
