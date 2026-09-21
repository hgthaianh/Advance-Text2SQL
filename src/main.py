from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from src.api.routes import auth, health, query
from src.auth.middleware import AuthenticationMiddleware
from src.config import get_settings

settings = get_settings()
app = FastAPI(title=settings.app_name, version="0.1.0")
app.add_middleware(AuthenticationMiddleware)
if settings.cors_origins:
    allow_all_origins = settings.cors_origins == ["*"]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=not allow_all_origins,
        allow_methods=["GET", "POST"],
        allow_headers=["Authorization", "Content-Type"],
    )
app.include_router(health.router)
app.include_router(auth.router)
app.include_router(query.router)
