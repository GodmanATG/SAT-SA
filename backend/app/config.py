from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    database_url: str = "postgresql://satsa:satsa@localhost:5432/satsa"

    class Config:
        env_file = ".env"

settings = Settings()
