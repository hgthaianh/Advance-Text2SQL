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
