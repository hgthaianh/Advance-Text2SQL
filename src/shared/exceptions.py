class Text2SQLError(Exception):
    """Base exception for domain errors; map to API errors when implemented."""


class QueryValidationError(Text2SQLError):
    pass


class PermissionDeniedError(Text2SQLError):
    pass


class UnsupportedIntentError(QueryValidationError):
    pass


class InvalidTableError(QueryValidationError, ValueError):
    pass


class InvalidColumnError(QueryValidationError, ValueError):
    pass


class UnsupportedOperatorError(QueryValidationError):
    pass


class UnsupportedAggregationError(QueryValidationError):
    pass


class MissingAggregationColumnError(QueryValidationError):
    pass


class JoinPathNotFoundError(QueryValidationError):
    pass


class AmbiguousJoinPathError(QueryValidationError):
    pass


class MalformedQueryIRError(QueryValidationError):
    pass


class FanoutRiskError(QueryValidationError):
    """An aggregate could be duplicated by a one-to-many join."""


class UnsupportedQueryError(QueryValidationError):
    """The deterministic pipeline cannot support the requested semantics."""


class MissingEntityError(QueryValidationError):
    """A query requires a value that was not present in the question."""


class AmbiguousQueryError(QueryValidationError):
    """Several schema interpretations remain equally plausible."""


class SQLValidationError(QueryValidationError):
    """A compiled statement is not safe for the read-only execution boundary."""


class QueryExecutionError(Text2SQLError):
    """Database execution failed without exposing internal connection details."""
