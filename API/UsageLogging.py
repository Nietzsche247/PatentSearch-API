from datetime import date
from uuid import uuid4

import redis
from django.conf import settings


def get_client_ip(request):
    x_forwarded_for = request.META.get("HTTP_X_FORWARDED_FOR")
    if x_forwarded_for:
        ip = x_forwarded_for.split(",")[0]
    else:
        ip = request.META.get("REMOTE_ADDR")
    return ip


def log_usage(request, endpoint, status_code, exception, fields_involved):
    d = date.today().isoformat()
    # endpoint = request.get_full_path().split("?")[0]
    # from API.models import APIUserKey
    # api_key = APIUserKey.objects.get_from_key(request.META['HTTP_X_API_KEY'])
    # ident = api_key.username
    try:
        usage_data = {
            "request_date": d,
            "client_ip": get_client_ip(request),
            "api_prefix": request.META["HTTP_X_API_KEY"].split(".")[0],
            "status_code": status_code,
            "request": endpoint,
            "user_agent": request.META["HTTP_USER_AGENT"],
            "method": request.META["REQUEST_METHOD"],
            "exception": exception,
        }
    except KeyError:
        return

    r = redis.StrictRedis(
        host=settings.REDIS_HOST,
        port=settings.REDIS_PORT,
        db=settings.REDIS_DATABASES["STATS"],
    )
    op_name = "request_{uid}".format(uid=str(uuid4()))
    pipe = r.pipeline()
    for key, value in usage_data.items():
        pipe.hset(name=op_name, key=key, value=value)
    field_set_name = f"field_analytics_{d}_{endpoint}"
    for field in fields_involved:
        pipe.zincrby(name=field_set_name, amount=1, value=field)
    pipe.execute()
