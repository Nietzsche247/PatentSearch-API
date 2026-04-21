from pvapi.settings.base import ALLOWED_HOSTS, env

from .base import *  # noqa: F403

DEBUG = True
ALLOWED_HOSTS = ALLOWED_HOSTS + ["localhost"]
