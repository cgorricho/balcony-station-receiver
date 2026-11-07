"""Rotating file log. The API key must never reach it."""

import logging
import re
from logging.handlers import RotatingFileHandler

from .config import Settings

MAX_BYTES = 1_000_000
BACKUP_COUNT = 4  # One active file plus four backups: 5 x 1 MB.
LOG_FILE = "receiver.log"
LOGGER_NAMES = ("receiver", "uvicorn")
KEY_PARAM = re.compile(r"([?&]key=)[^&\s\"']+")


class RedactingFormatter(logging.Formatter):
    """Mask the API key and ?key= values, including inside tracebacks."""

    def __init__(self, api_key: str):
        super().__init__("%(asctime)s %(levelname)s %(name)s %(message)s")
        self.api_key = api_key

    def format(self, record: logging.LogRecord) -> str:
        text = super().format(record).replace(self.api_key, "[redacted]")
        return KEY_PARAM.sub(r"\1[redacted]", text)


def configure_logging(settings: Settings) -> None:
    settings.log_dir.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(
        settings.log_dir / LOG_FILE,
        maxBytes=MAX_BYTES,
        backupCount=BACKUP_COUNT,
        encoding="utf-8",
        delay=True,
    )
    handler.setFormatter(RedactingFormatter(settings.api_key))
    for name in LOGGER_NAMES:
        logger = logging.getLogger(name)
        for old_handler in list(logger.handlers):
            logger.removeHandler(old_handler)
            old_handler.close()
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
        logger.propagate = False
