"""Flask web server for the SPS manager UI."""

import logging
import sys

from backend.web import create_app


def configure_logging() -> None:
    """Configure timestamped stdout logging."""
    logging.basicConfig(
        level=logging.INFO,
        stream=sys.stdout,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


def main() -> None:
    """Start the web application without the development reloader."""
    configure_logging()
    app = create_app()
    app.run(host="0.0.0.0", port=5000, debug=False, use_reloader=False)


if __name__ == "__main__":
    main()
