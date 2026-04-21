import os

import requests

from pvapi.settings.base import *  # noqa: F403
from pvapi.settings.base import ALLOWED_HOSTS

ENV = "PROD"
DEBUG = False
ELB_HEALTHCHECK_HOSTNAMES = [
    ip
    for network in requests.get(os.environ["ECS_CONTAINER_METADATA_URI"]).json()[
        "Networks"
    ]
    for ip in network["IPv4Addresses"]
]
ALLOWED_HOSTS = ALLOWED_HOSTS + ["search.patentsview.org"] + ELB_HEALTHCHECK_HOSTNAMES

CSRF_TRUSTED_ORIGINS = [
    "https://search.patentsview.org",
]
