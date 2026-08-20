from fastapi import FastAPI

from autoposter.config.schema import Config, Secrets
from autoposter.intake.routes import router


def create_app(config: Config, session_factory, secrets: Secrets) -> FastAPI:
    app = FastAPI(title="autoposter")
    app.state.config = config
    app.state.session_factory = session_factory
    app.state.secrets = secrets
    app.include_router(router)
    return app
