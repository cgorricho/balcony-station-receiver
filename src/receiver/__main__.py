"""Start the server: python -m receiver"""

import uvicorn

from .config import load_settings
from .main import create_app


def main() -> None:
    settings = load_settings()
    # The app logs each request itself, so uvicorn's access log stays off.
    # log_config=None leaves logging to configure_logging. proxy_headers=False
    # because the app applies STATION_FORWARDED_ALLOW_IPS itself.
    uvicorn.run(
        create_app(settings),
        host=settings.host,
        port=settings.port,
        access_log=False,
        log_config=None,
        proxy_headers=False,
    )


if __name__ == "__main__":
    main()
