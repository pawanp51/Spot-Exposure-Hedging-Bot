import os
from dotenv import load_dotenv

# Load .env file if present
load_dotenv()

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")

# Default threshold percent if not provided
try:
    THRESHOLD_PERCENT = float(os.getenv("THRESHOLD_PERCENT", "10"))
except ValueError:
    raise ValueError("Invalid THRESHOLD_PERCENT in environment; must be a number.")

# Validate required credentials
_missing = []
for key, val in [
    ("TELEGRAM_BOT_TOKEN", TELEGRAM_BOT_TOKEN)
]:
    if not val:
        _missing.append(key)
if _missing:
    raise RuntimeError(f"Missing required config keys: {', '.join(_missing)}")