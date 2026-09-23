# Architecture

The request path is deterministic:

`API → authenticated identity → normalization/date/number parsing → scope routing → schema linking → entity extraction → QueryIR → permission resolution and row policy → join planning → SQL compilation → SQL validation → read-only execution → QueryResult`

`schema/schema_spec.yaml` is the schema source of truth. The registry validates
all table and column references before planning. The linker and QueryIR builder
do not execute SQL or make authorization decisions.

Permission identity comes from authenticated middleware, not the request body.
The pipeline resolves table/column policies and injects row scope before SQL is
compiled. The builder emits parameterized SQLAlchemy `Select` statements only;
the validator rejects raw SQL and the executor runs PostgreSQL queries in a
read-only transaction with a statement timeout.

The API converts controlled failures into stable 403, 422, or 503 responses
without exposing SQL, parameters, identities, or internal exception details.
Observability records only a bounded set of safe terminal metadata.
