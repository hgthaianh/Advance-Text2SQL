import pytest

from scripts.seed_execution_eval import seed


def test_execution_seed_refuses_non_dedicated_database():
    with pytest.raises(ValueError, match='end in _eval'):
        seed('postgresql+psycopg://user:pass@localhost:5432/text2sql')
