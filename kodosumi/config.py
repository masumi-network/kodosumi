import json
import os
from pathlib import Path
from typing import List

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


LOG_LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")


class Settings(BaseSettings):

    EXEC_DIR: str = "./data/execution"

    SPOOLER_LOG_FILE: str = "./data/spooler.log"
    SPOOLER_LOG_FILE_LEVEL: str = "DEBUG"
    SPOOLER_STD_LEVEL: str = "INFO"
    
    RAY_SERVER: str = "localhost:6379"
    RAY_DASHBOARD: str = "http://localhost:8265"
    RAY_HTTP: str = "http://localhost:8001"

    APP_LOG_FILE: str = "./data/app.log"
    APP_LOG_FILE_LEVEL: str = "DEBUG"
    APP_STD_LEVEL: str = "INFO"
    CORS_ORIGINS: List[str] = ["*"]

    APP_SERVER: str = "http://localhost:3370"
    APP_RELOAD: bool = False

    UVICORN_LEVEL: str = "WARNING"
    SECRET_KEY: str = "top secret -- change this in production"

    WAIT_FOR_JOB: int = 10
    
    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="KODO_",
        env_file_encoding="utf-8",
        extra="ignore"
    )

    @field_validator("EXEC_DIR", mode="before")
    def make_dir(cls, v):
        if v:
            Path(v).mkdir(parents=True, exist_ok=True)
        return v

    @field_validator("SPOOLER_LOG_FILE", mode="before")
    def make_parent(cls, v):
        if v:
            Path(v).parent.mkdir(parents=True, exist_ok=True)
        return v

    @field_validator("CORS_ORIGINS", mode="before")
    def string_to_list(cls, v):
        if isinstance(v, str):
            return [s.strip() for s in v.split(',')]
        return v


class InternalSettings(Settings):

    def __init__(self, **kwargs):
        for field in self.model_fields:
            env_var = f"iKODO_{field}"
            if env_var in os.environ:
                kwargs[field] = json.loads(os.environ[env_var])
        super().__init__(**kwargs)
