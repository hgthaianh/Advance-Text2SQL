from fastapi import FastAPI

from src.api.routes import health, query
from src.config import get_settings

app = FastAPI(title=get_settings().app_name, version="0.1.0")
app.include_router(health.router)
app.include_router(query.router)
