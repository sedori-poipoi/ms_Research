import logging
import os
import stat
from pathlib import Path

from dotenv import load_dotenv

logger = logging.getLogger(__name__)


def warn_if_insecure_env_permissions(env_path=".env"):
    path = Path(env_path)
    if not path.exists():
        return

    try:
        mode = stat.S_IMODE(path.stat().st_mode)
    except OSError as exc:
        logger.warning("Could not inspect %s permissions: %s", path, exc)
        return

    if mode & (stat.S_IRWXG | stat.S_IRWXO):
        logger.warning(
            "%s permissions are broader than recommended: %s. Use chmod 600 %s.",
            path,
            oct(mode),
            path,
        )


def load_env_file(env_path=".env"):
    load_dotenv(env_path)
    warn_if_insecure_env_permissions(env_path)
