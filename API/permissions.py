from rest_framework.permissions import BasePermission
from rest_framework_api_key.permissions import BaseHasAPIKey

from .models import APIUserKey


class HasUserAPIKey(BaseHasAPIKey):
    model = APIUserKey


class HasValidAPIKey(BasePermission):
    def has_permission(self, request, view):
        api_key = request.META.get("HTTP_X_API_KEY", None)

        if not api_key:
            return False

        try:
            key = APIUserKey.objects.get_from_key(api_key)
            return not key.revoked
        except APIUserKey.DoesNotExist:
            return False


class HasAPIKeyCreationPermission(BasePermission):
    def has_permission(self, request, view):
        api_key = request.META.get("HTTP_X_API_KEY", None)
        if not api_key:
            return False

        key = APIUserKey.objects.get_from_key(api_key)

        return key.can_create_key
