"""App settings, read from environment variables (or a .env file you load yourself)."""
import os


def _bool(name: str, default: bool) -> bool:
    return os.getenv(name, str(default)).lower() in {"1", "true", "yes"}


DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./dropkit.db")
JWT_SECRET = os.getenv("JWT_SECRET", "dev-only-secret-change-me-before-deploying")
JWT_EXPIRE_MINUTES = int(os.getenv("JWT_EXPIRE_MINUTES", "10080"))  # 7 days

# Public URL of this API, used to build click-tracking and unsubscribe links.
BASE_URL = os.getenv("BASE_URL", "http://localhost:8000")

# Background scheduler that fires drops at release_at.
SCHEDULER_ENABLED = _bool("SCHEDULER_ENABLED", True)
SCHEDULER_INTERVAL_SECONDS = int(os.getenv("SCHEDULER_INTERVAL_SECONDS", "30"))

# Messaging providers. Leave blank to print messages to the console instead.
RESEND_API_KEY = os.getenv("RESEND_API_KEY", "")
EMAIL_FROM = os.getenv("EMAIL_FROM", "drops@example.com")
TWILIO_ACCOUNT_SID = os.getenv("TWILIO_ACCOUNT_SID", "")
TWILIO_AUTH_TOKEN = os.getenv("TWILIO_AUTH_TOKEN", "")
TWILIO_FROM_NUMBER = os.getenv("TWILIO_FROM_NUMBER", "")
