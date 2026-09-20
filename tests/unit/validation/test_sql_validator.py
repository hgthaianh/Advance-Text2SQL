import pytest
from sqlalchemy import insert, select, table, text

from src.shared.exceptions import SQLValidationError
from src.validation.sql_validator import ReadOnlySQLValidator


def test_read_only_validator_accepts_typed_select():
    employees = table('employees')

    ReadOnlySQLValidator().validate(select(employees).select_from(employees))


@pytest.mark.parametrize('statement', [
    insert(table('employees')).values(id=1),
    select(text('id')).select_from(table('employees')),
])
def test_read_only_validator_rejects_non_select_or_raw_sql(statement):
    with pytest.raises(SQLValidationError):
        ReadOnlySQLValidator().validate(statement)
