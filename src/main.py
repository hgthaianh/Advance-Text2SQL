from fastapi import FastAPI

from src.api.routes import auth, health, query
from src.auth.middleware import AuthenticationMiddleware
from src.config import get_settings

app = FastAPI(title=get_settings().app_name, version="0.1.0")
app.add_middleware(AuthenticationMiddleware)
app.include_router(health.router)
app.include_router(auth.router)
app.include_router(query.router)
