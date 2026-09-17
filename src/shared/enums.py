from enum import StrEnum


class Intent(StrEnum):
    SELECT = "select"
    AGGREGATE = "aggregate"
    UNKNOWN = "unknown"


class FilterOperator(StrEnum):
    EQ = "eq"
    NE = "ne"
    GT = "gt"
    GTE = "gte"
    LT = "lt"
    LTE = "lte"
    IN = "in"
    LIKE = "like"


class SortDirection(StrEnum):
    ASC = "asc"
    DESC = "desc"
