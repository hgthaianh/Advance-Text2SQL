"""Run with python -m scripts.init_db from the repository root."""
from src.database.models import Base
from src.database.session import get_engine


def main() -> None:
    # TODO: import ORM models here. Add migrations when the schema exists.
    engine = get_engine()
    try:
        Base.metadata.create_all(engine)
        print("Database reachable; registered tables initialized (currently none).")
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
