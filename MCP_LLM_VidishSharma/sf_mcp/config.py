import os
from dotenv import load_dotenv

load_dotenv()


def _require(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


class Settings:
    def __init__(self):
        self.sf_login_url = os.environ.get("SF_LOGIN_URL", "https://login.salesforce.com")
        self.sf_client_id = _require("SF_CLIENT_ID")
        self.sf_client_secret = _require("SF_CLIENT_SECRET")
        self.groq_api_key = _require("GROQ_API_KEY")
        self.groq_model = os.environ.get("GROQ_MODEL", "openai/gpt-oss-120b")


settings = Settings()
