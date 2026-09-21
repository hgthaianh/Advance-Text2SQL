from abc import ABC, abstractmethod
from typing import Sequence

from src.permission.models import PermissionContext
from src.query.ir import QueryIR
from src.shared.exceptions import PermissionDeniedError

# Default list of sensitive columns (salary, bank account, PII) requiring explicit authorization
SENSITIVE_COLUMNS: set[str] = {
    "salary",
    "base_salary",
    "gross_salary",
    "net_salary",
    "bank_account",
    "bank_account_number",
    "bank_name",
    "ssn",
    "social_security_number",
    "tax_id",
    "tax_code",
    "national_id",
    "passport_number",
    "credit_card",
    "password_hash",
    "secret",
}


class ColumnPolicy(ABC):
    @abstractmethod
    def validate(self, query: QueryIR, permissions: PermissionContext) -> None:
        """Validate column access against permission rules and sensitive column policies."""
        raise NotImplementedError


class DefaultColumnPolicy(ColumnPolicy):
    """Enforces column-level allow/deny policies and blocks unauthorized sensitive column access."""

    def __init__(self, sensitive_columns: Sequence[str] | None = None) -> None:
        self.sensitive_columns = set(sensitive_columns) if sensitive_columns is not None else SENSITIVE_COLUMNS

    def validate(self, query: QueryIR, permissions: PermissionContext) -> None:
        """Validate all referenced columns in QueryIR (select, filters, group_by, order_by).

        Raises PermissionDeniedError deterministically if any referenced column is denied or unauthorized.
        """
        referenced_columns = self._extract_referenced_columns(query)
        allowed_cols_map = permissions.allowed_columns
        denied_cols_set = set(permissions.denied_columns)

        for table, col in referenced_columns:
            full_col_name = f"{table}.{col}" if table else col

            # 1. Explicit denied columns check
            if col in denied_cols_set or full_col_name in denied_cols_set:
                raise PermissionDeniedError(f"Access denied to explicitly forbidden column '{col}'.")

            # 2. Sensitive column check: sensitive columns must be explicitly allowed in allowed_columns
            is_sensitive = col in self.sensitive_columns or full_col_name in self.sensitive_columns
            if is_sensitive:
                if not self._is_sensitive_allowed(table, col, allowed_cols_map):
                    raise PermissionDeniedError(f"Unauthorized access to sensitive column '{col}'.")

            # 3. Allowlist check if allowed_columns map is non-empty
            if allowed_cols_map:
                if not self._is_column_allowed(table, col, allowed_cols_map):
                    raise PermissionDeniedError(f"Access denied to column '{col}' on table '{table}'.")

    def _is_sensitive_allowed(self, table: str, col: str, allowed_cols_map: dict[str, list[str]]) -> bool:
        if not allowed_cols_map:
            return False

        if "*" in allowed_cols_map:
            allowed_list = allowed_cols_map["*"]
            if "*" in allowed_list or col in allowed_list or f"{table}.{col}" in allowed_list:
                return True

        if table and table in allowed_cols_map:
            allowed_list = allowed_cols_map[table]
            if "*" in allowed_list or col in allowed_list or f"{table}.{col}" in allowed_list:
                return True

        return False

    def _is_column_allowed(self, table: str, col: str, allowed_cols_map: dict[str, list[str]]) -> bool:
        if not allowed_cols_map:
            return True

        if "*" in allowed_cols_map:
            allowed_list = allowed_cols_map["*"]
            if "*" in allowed_list or col in allowed_list or f"{table}.{col}" in allowed_list:
                return True

        if table and table in allowed_cols_map:
            allowed_list = allowed_cols_map[table]
            if "*" in allowed_list or col in allowed_list or f"{table}.{col}" in allowed_list:
                return True

        return False

    def _extract_referenced_columns(self, query: QueryIR) -> list[tuple[str, str]]:
        columns: list[tuple[str, str]] = []

        for proj in query.select:
            col_str = proj if isinstance(proj, str) else proj.column
            if col_str and col_str != "*":
                columns.append(self._parse_col(col_str))

        for qf in query.filters:
            # Trusted permission constraints are schema-validated by the
            # planner but are not user projections and need not appear in a
            # user's display-column allowlist.
            if qf.column and qf.source != 'permission':
                columns.append(self._parse_col(qf.column))

        for gb in query.group_by:
            if gb:
                columns.append(self._parse_col(gb))

        for ob in query.order_by:
            col_str = ob.column if hasattr(ob, "column") else str(ob)
            if col_str:
                columns.append(self._parse_col(col_str))

        return columns

    def _parse_col(self, col_str: str) -> tuple[str, str]:
        if "." in col_str:
            table, col = col_str.rsplit(".", 1)
            return table, col
        return "", col_str

