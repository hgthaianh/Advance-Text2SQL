"""Provision the bundled deterministic PostgreSQL dataset.

The bundled schema starts by dropping its owned schemas.  To prevent an
accidental destructive local operation, that action is available only with an
explicit ``--reset`` flag and is intended for an empty disposable database.
"""
import argparse
from pathlib import Path

from sqlalchemy import text

from src.database.session import get_engine
from src.schema.registry import ROOT


def _run_script(script: Path) -> None:
    # psycopg accepts the project's trusted, versioned PostgreSQL script as a
    # single unit, preserving function bodies and explicit transactions.
    engine = get_engine()
    try:
        with engine.connect() as connection:
            connection.connection.driver_connection.execute(script.read_text(encoding='utf-8'))
    finally:
        engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--reset', action='store_true', help='Apply destructive bundled schema + deterministic seed to a disposable DB')
    args = parser.parse_args()
    if not args.reset:
        raise SystemExit('Refusing destructive bootstrap without --reset. Use a fresh disposable database.')
    engine = get_engine()
    try:
        with engine.connect() as connection:
            connection.execute(text('SELECT 1'))
    finally:
        engine.dispose()
    _run_script(ROOT / 'dataset_v2/sql/01_schema.sql')
    _run_script(ROOT / 'dataset_v2/sql/02_seed_data.sql')
    print('Deterministic schema and seed data provisioned.')


if __name__ == "__main__":
    main()
