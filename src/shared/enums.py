from enum import StrEnum


class Intent(StrEnum):
    LOOKUP = "lookup"
    AVG = "avg"
    COUNT = "count"
    SUM = "sum"
    REVENUE = "revenue"
    AVERAGE = "average"
    TOP_N = "top_n"
    DETAIL = "detail"
    LIST = "list"
    FILTER = "filter"
    COMPARISON = "comparison"
    TREND = "trend"
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


class Aggregation(StrEnum):
    COUNT = "count"
    SUM = "sum"
    AVG = "avg"


class PermissionScope(StrEnum):
    GLOBAL = "GLOBAL"
    BRANCH = "BRANCH"
    DEPARTMENT = "DEPARTMENT"
    TEAM = "TEAM"
    SELF = "SELF"
