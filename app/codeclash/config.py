from pydantic import model_validator
from pydantic_settings import BaseSettings

# Dev default: fine for localhost, fatal in prod (anyone with the repo could forge
# a JWT), so the guard below refuses it when APP_ENV=production.
DEV_JWT_SECRET = "dev-secret-change-me"


class Settings(BaseSettings):
    database_url: str = "postgresql://codeclash:codeclash@postgres:5432/codeclash"
    redis_url: str = "redis://redis:6379/0"
    jwt_secret: str = DEV_JWT_SECRET
    app_env: str = "dev"  # set APP_ENV=production in prod to harden cookies + secret

    @model_validator(mode="after")
    def _require_real_secret_in_prod(self) -> "Settings":
        # An HS256 key under 32 chars is brute-forceable; also reject the dev default.
        if self.app_env == "production" and (
            self.jwt_secret == DEV_JWT_SECRET or len(self.jwt_secret) < 32
        ):
            raise RuntimeError(
                "JWT_SECRET must be a strong, non-default value (>=32 chars) in "
                "production. Generate one in .env:  JWT_SECRET=$(openssl rand -hex 32)"
            )
        return self


settings = Settings()
