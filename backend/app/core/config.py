import os
from typing import List, Dict, ClassVar

from dotenv import load_dotenv
from pydantic_settings import BaseSettings
from pydantic import computed_field

load_dotenv()

ENV: str = ""


class Configs(BaseSettings):
    # base
    ENV: str = os.getenv("ENV", "dev")
    API: str = "/api"
    API_V1_STR: str = "/api/v1"
    PROJECT_NAME: str = "fca-api"
    ENV_DATABASE_MAPPER: Dict[str, str] = {
        "prod": "liveboost",
        "stage": "liveboost",
        "dev": "dev-fca",
        "test": "test-fca",
    }
    DB_ENGINE_MAPPER: Dict[str, str] = {
        "postgresql": "postgresql",
        "mysql": "mysql+pymysql",
    }

    PROJECT_ROOT: str = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

    # date
    DATETIME_FORMAT: str = "%Y-%m-%dT%H:%M:%S"
    DATE_FORMAT: str = "%Y-%m-%d"

    # auth
    SECRET_KEY: str = os.getenv("SECRET_KEY", "")
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60 * 24 * 30  # 60 minutes * 24 hours * 30 days = 30 days

    # CORS
    BACKEND_CORS_ORIGINS: List[str] = [
        "https://www.aitherhub.com",
        "https://aitherhub.com",
        "http://localhost:3000",
        "http://localhost:5173",
        "http://127.0.0.1:3000",
        "http://127.0.0.1:5173",
    ]

    # database
    DB: str = os.getenv("DB", "postgresql")
    DB_USER: str = os.getenv("DB_USER", "")
    DB_PASSWORD: str = os.getenv("DB_PASSWORD", "")
    DB_HOST: str = os.getenv("DB_HOST", "")
    DB_PORT: str = os.getenv("DB_PORT", "3306")
    
    DATABASE_URI_FORMAT: str = "{db_engine}://{user}:{password}@{host}:{port}/{database}"

    # Support both DATABASE_URL (from env) and DATABASE_URI (constructed)
    DATABASE_URL: str = os.getenv("DATABASE_URL", "")

    # find query
    PAGE: int = 1
    PAGE_SIZE: int = 20
    ORDERING: str = "-id"

    @computed_field
    @property
    def DB_ENGINE(self) -> str:
        return self.DB_ENGINE_MAPPER.get(self.DB, "postgresql")

    @computed_field
    @property
    def DATABASE_URI(self) -> str:
        if self.DATABASE_URL:
            return self.DATABASE_URL
        return self.DATABASE_URI_FORMAT.format(
            db_engine=self.DB_ENGINE,
            user=self.DB_USER,
            password=self.DB_PASSWORD,
            host=self.DB_HOST,
            port=self.DB_PORT,
            database=self.ENV_DATABASE_MAPPER.get(self.ENV, "dev-fca"),
        )

    class Config:
        case_sensitive = True


class TestConfigs(Configs):
    ENV: str = "test"


configs = Configs()

if ENV == "prod":
    pass
elif ENV == "stage":
    pass
elif ENV == "test":
    setting = TestConfigs()
