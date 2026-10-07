"""Logging: one rotating file under the data directory, plus stderr.

launchd captures stderr too, but a file that rotates on its own is what you read
a week later. Handlers hang off the `clefline` logger only, so uvicorn's own
logging is untouched. Safe to call more than once.
"""

import logging
import logging.handlers
import sys
from pathlib import Path

import config
import paths

FORMAT = "%(asctime)s %(levelname)-7s %(name)s %(message)s"
MAX_BYTES = 5 * 1024 * 1024
BACKUPS = 3


def configure(
    directory: Path | None = None, level: str | None = None, file: bool = True
) -> list[logging.Handler]:
    """Attach stderr (and, unless `file=False`, a rotating file) to `clefline`."""
    logger = logging.getLogger("clefline")
    for handler in list(logger.handlers):
        logger.removeHandler(handler)
        handler.close()
    logger.setLevel(level or config.LOG_LEVEL)

    formatter = logging.Formatter(FORMAT)
    handlers: list[logging.Handler] = [logging.StreamHandler(sys.stderr)]
    failure = None
    if file:
        target = directory if directory is not None else paths.DATA / "logs"
        try:
            target.mkdir(parents=True, exist_ok=True)
            handlers.append(logging.handlers.RotatingFileHandler(
                target / "clefline.log", maxBytes=MAX_BYTES, backupCount=BACKUPS,
                encoding="utf-8",
            ))
        except OSError as error:
            failure = error   # the server must still start; stderr keeps working

    for handler in handlers:
        handler.setFormatter(formatter)
        logger.addHandler(handler)
    if failure is not None:
        logger.warning("could not open the log file (%s); logging to stderr only", failure.strerror)
    return handlers
