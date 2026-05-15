import logging
import sys

logging.basicConfig(
    level=logging.INFO,
    format="%(levelname)s %(name)s: %(message)s",
    stream=sys.stderr,
)

def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
