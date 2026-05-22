from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
    )

    # Facebook
    fb_email: str = ""
    fb_password: str = ""

    # LLM
    openai_api_key: str = ""
    anthropic_api_key: str = ""
    gemini_api_key: str = ""
    model_name: str = "gpt-4o-mini"
    llm_provider: str = "openai"

    # Storage
    cookie_path: str = "data/browser_profiles"
    db_path: str = "data/cache.db"


settings = Settings()
