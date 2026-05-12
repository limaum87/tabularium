from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Configurações da aplicação lidas do .env."""

    # Banco
    DATABASE_URL: str = "mysql+pymysql://tabularium:tabularium123@mysql:3306/tabularium"

    # API
    API_HOST: str = "0.0.0.0"
    API_PORT: int = 8000
    API_SECRET_KEY: str = "change-me"
    API_DEBUG: bool = True

    # JWT
    JWT_SECRET_KEY: str = "change-me"
    JWT_ALGORITHM: str = "HS256"
    JWT_EXPIRATION_HOURS: int = 8

    # Admin padrão (seed)
    ADMIN_EMAIL: str = "admin@tabularium.local"
    ADMIN_PASSWORD: str = "admin123"

    # Collector
    COLLECTOR_API_TOKEN: str = "token-do-collector"

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


settings = Settings()
