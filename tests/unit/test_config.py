from src.config import Settings


def test_database_config_loads_from_environment(monkeypatch):
    values = {
        "POSTGRES_HOST": "db.test", "POSTGRES_PORT": "5433",
        "POSTGRES_DB": "example", "POSTGRES_USER": "tester",
        "POSTGRES_PASSWORD": "p@ss:/word",
    }
    for key, value in values.items():
        monkeypatch.setenv(key, value)
    url = Settings(_env_file=None).database_url
    assert url.drivername == "postgresql+psycopg"
    assert url.host == "db.test"
    assert url.port == 5433
    assert url.database == "example"
    assert url.username == "tester"
    assert url.password == values["POSTGRES_PASSWORD"]
