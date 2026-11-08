"""Settings loaded from the environment and the project's .env file."""

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

from .derived import DEFAULT_LUX_PER_PPFD, DEFAULT_SUN_THRESHOLD_LUX

PROJECT_ROOT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class Settings:
    api_key: str
    db_path: Path
    host: str = "0.0.0.0"
    port: int = 8765
    log_dir: Path = PROJECT_ROOT / "logs"
    tz: str = "America/New_York"
    lux_per_ppfd: float = DEFAULT_LUX_PER_PPFD
    sun_threshold_lux: float = DEFAULT_SUN_THRESHOLD_LUX
    # Comma-separated proxy addresses or networks whose X-Forwarded-For is
    # trusted. Empty trusts no one.
    forwarded_allow_ips: str = "127.0.0.1"


def _resolve(value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else PROJECT_ROOT / path


def load_settings() -> Settings:
    load_dotenv(PROJECT_ROOT / ".env")
    api_key = os.environ.get("STATION_API_KEY", "")
    if not api_key:
        raise RuntimeError("STATION_API_KEY is not set; see .env.example")
    return Settings(
        api_key=api_key,
        db_path=_resolve(os.environ.get("STATION_DB_PATH", "data/station.db")),
        host=os.environ.get("STATION_HOST", "0.0.0.0"),
        port=int(os.environ.get("STATION_PORT", "8765")),
        log_dir=_resolve(os.environ.get("STATION_LOG_DIR", "logs")),
        tz=os.environ.get("STATION_TZ", "America/New_York"),
        lux_per_ppfd=float(os.environ.get("STATION_LUX_PER_PPFD", DEFAULT_LUX_PER_PPFD)),
        sun_threshold_lux=float(
            os.environ.get("STATION_SUN_THRESHOLD_LUX", DEFAULT_SUN_THRESHOLD_LUX)
        ),
        forwarded_allow_ips=os.environ.get("STATION_FORWARDED_ALLOW_IPS", "127.0.0.1"),
    )
