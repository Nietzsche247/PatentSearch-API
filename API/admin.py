from django.contrib import admin

# Register your models here.
from rest_framework_api_key.admin import APIKeyModelAdmin
from rest_framework_api_key.models import APIKey

from API.models import APIUserKey


@admin.register(APIUserKey)
class OrganizationAPIKeyModelAdmin(APIKeyModelAdmin):
    pass


admin.site.unregister(APIKey)
