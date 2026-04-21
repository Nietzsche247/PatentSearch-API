from pvapi.settings.base import *  # noqa: F403
from pvapi.settings.base import ALLOWED_HOSTS, APP_PORT, INSTALLED_APPS, LOGGING

DEBUG = True
ALLOWED_HOSTS = ALLOWED_HOSTS + ["localhost"]

# Application definition
INSTALLED_APPS = INSTALLED_APPS + ["django_extensions"]
LOGGING["loggers"]["root"]["level"] = "DEBUG"
LOGGING["loggers"]["API"]["level"] = "DEBUG"
LOGGING["handlers"]["console"]["level"] = "DEBUG"

APP_URL = f"http://localhost:{APP_PORT}"
CSRF_TRUSTED_ORIGINS = [APP_URL]
CORS_ORIGIN_WHITELIST = (APP_URL,)
