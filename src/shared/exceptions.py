class Text2SQLError(Exception):
    """Base exception for domain errors; map to API errors when implemented."""


class QueryValidationError(Text2SQLError):
    pass


class PermissionDeniedError(Text2SQLError):
    pass
