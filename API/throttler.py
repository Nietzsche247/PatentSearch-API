from rest_framework.throttling import SimpleRateThrottle

from API.models import APIUserKey


class APIKeyThrottle(SimpleRateThrottle):
    scope = "key"

    def get_ident(self, request):
        key = request.META["HTTP_X_API_KEY"]
        if key is not None:
            api_key = APIUserKey.objects.get_from_key(key)
            ident = api_key.username
        else:
            ident = super().get_ident(request)
        return ident

    def get_cache_key(self, request, view):
        try:
            key = request.META["HTTP_X_API_KEY"]
            prefix, suffix = key.split(".")
        except (KeyError, ValueError):
            prefix = super().get_ident(request)
        return self.cache_format % {"scope": self.scope, "ident": prefix}
