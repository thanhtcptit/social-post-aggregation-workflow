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

    # Debug
    verbose: bool = False

    # Storage
    cookie_path: str = "data/browser_profiles"
    db_path: str = "data/cache.db"

    # Chrome profile to read cookies from during manual login
    # Run `chrome://version` in Chrome to see your profile path.
    # Use the folder name only, e.g. "Default", "Profile 1", "Profile 2".
    chrome_profile: str = "Profile 1"

    # Fallback: paste Facebook cookies as a JSON array when Chrome remote
    # debugging is blocked.  Format (minimum required fields):
    # FB_COOKIES=[{"name":"c_user","value":"...","domain":".facebook.com","path":"/"},
    #             {"name":"xs","value":"...","domain":".facebook.com","path":"/"}]
    fb_cookies: str = ""


settings = Settings()
