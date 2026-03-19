import os
from dotenv import load_dotenv

load_dotenv()

OPENSTATES_API_KEY: str = os.environ["OPENSTATES_API_KEY"]
OPENSTATES_BASE_URL: str = "https://v3.openstates.org"

MONGODB_URI: str = os.environ.get("MONGODB_URI", "mongodb://localhost:27017")
MONGODB_DB: str = os.environ.get("MONGODB_DB", "state_pulse")

REDIS_URL: str = os.environ.get("REDIS_URL", "redis://localhost:6379/0")

ALL_JURISDICTIONS: list[str] = [
    "al", "ak", "az", "ar", "ca", "co", "ct", "de", "fl", "ga",
    "hi", "id", "il", "in", "ia", "ks", "ky", "la", "me", "md",
    "ma", "mi", "mn", "ms", "mo", "mt", "ne", "nv", "nh", "nj",
    "nm", "ny", "nc", "nd", "oh", "ok", "or", "pa", "ri", "sc",
    "sd", "tn", "tx", "ut", "vt", "va", "wa", "wv", "wi", "wy",
    "dc",
]

_raw = os.environ.get("JURISDICTIONS", "")
JURISDICTIONS: list[str] = (
    [j.strip().lower() for j in _raw.split(",") if j.strip()]
    if _raw
    else ALL_JURISDICTIONS
)

SYNC_LOOKBACK_HOURS: int = int(os.environ.get("SYNC_LOOKBACK_HOURS", "24"))
PAGE_SIZE: int = int(os.environ.get("PAGE_SIZE", "20"))
