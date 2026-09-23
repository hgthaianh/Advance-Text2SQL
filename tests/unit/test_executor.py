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


def test_postgresql_execution_starts_a_read_only_transaction_before_selecting():
    class Result:
        def mappings(self):
            return self

        def fetchmany(self, _size):
            return [{'id': 1}]

        @staticmethod
        def keys():
            return ['id']

    class Connection:
        class dialect:
            name = 'postgresql'

        def __init__(self):
            self.commands = []

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def exec_driver_sql(self, statement):
            self.commands.append(statement)

        @staticmethod
        def execute(_statement, _params):
            return Result()

    class Engine:
        def __init__(self):
            self.connection = Connection()

        def connect(self):
            return self.connection

    engine = Engine()
    result = ReadOnlyExecutor(engine, timeout_ms=1234).execute(CompiledQuery(
        sql='SELECT 1', params={}, statement=select(1),
    ))

    assert result.rows == [{'id': 1}]
    assert engine.connection.commands == [
        'SET TRANSACTION READ ONLY',
        'SET LOCAL statement_timeout = 1234',
    ]
