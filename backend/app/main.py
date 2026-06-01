from fastapi import FastAPI

from app.api import agents, bot, companies, context, processing, telegram_import
from app.config import get_settings


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title=settings.app_name)
    app.include_router(companies.router)
    app.include_router(telegram_import.router)
    app.include_router(processing.router)
    app.include_router(context.router)
    app.include_router(agents.router)
    app.include_router(bot.router)
    return app


app = create_app()

