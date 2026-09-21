from abc import ABC, abstractmethod

from sqlalchemy.sql import Select, visitors
from sqlalchemy.sql.elements import TextClause
from src.permission.models import PermissionContext
from src.shared.exceptions import SQLValidationError


class SQLValidator(ABC):
    @abstractmethod
    def validate(self, statement: Select, permissions: PermissionContext) -> None:
        """TODO: implement this module contract without LLMs."""
        raise NotImplementedError


class ReadOnlySQLValidator(SQLValidator):
    """Validate the typed SQLAlchemy boundary before database execution.

    The planner/builder only produce ``Select`` objects and bind user values.
    This validator enforces that contract again at the execution boundary so a
    caller cannot substitute textual SQL or a DDL/DML statement.
    """

    def validate(self, statement: Select, permissions: PermissionContext | None = None) -> None:
        if not isinstance(statement, Select):
            raise SQLValidationError('Only SQLAlchemy SELECT statements may be executed')
        if statement.get_final_froms() == []:
            raise SQLValidationError('SELECT requires a validated FROM clause')

        unsafe = False

        def visit_textclause(_: TextClause) -> None:
            nonlocal unsafe
            unsafe = True

        visitors.traverse(statement, {}, {'textclause': visit_textclause})
        if unsafe:
            raise SQLValidationError('Raw SQL fragments are not permitted')
