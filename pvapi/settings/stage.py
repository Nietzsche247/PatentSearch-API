import os

import requests

from pvapi.settings.base import *  # noqa: F403
from pvapi.settings.base import ALLOWED_HOSTS

ENV = "STAGE"
DEBUG = False
ELB_HEALTHCHECK_HOSTNAMES = [
    ip
    for network in requests.get(os.environ["ECS_CONTAINER_METADATA_URI"]).json()[
        "Networks"
    ]
    for ip in network["IPv4Addresses"]
]
ALLOWED_HOSTS = (
    ALLOWED_HOSTS
    + [
        "search-test.patentsview.org",
        "search-staging.patentsview.org",
    ]
    + ELB_HEALTHCHECK_HOSTNAMES
)

CSRF_TRUSTED_ORIGINS = [
    "https://search-test.patentsview.org",
    "https://search-staging.patentsview.org",
]
