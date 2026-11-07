"""Start the server: python -m receiver"""

import uvicorn

from .config import load_settings
from .main import create_app


def main() -> None:
    settings = load_settings()
    # access_log=False keeps ?key= out of the logs. log_config=None leaves
    # logging to configure_logging, and works under pythonw, which has no console.
    uvicorn.run(
        create_app(settings),
        host=settings.host,
        port=settings.port,
        access_log=False,
        log_config=None,
    )


if __name__ == "__main__":
    main()
