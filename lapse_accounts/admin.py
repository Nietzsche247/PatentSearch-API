from django.contrib import admin

from lapse_accounts.models import AccountKey, MonthlyUsage


@admin.register(AccountKey)
class AccountKeyAdmin(admin.ModelAdmin):
    list_display = ("email", "plan", "prefix", "supabase_user_id", "created_at", "rotated_from")
    search_fields = ("email", "supabase_user_id", "api_key__prefix")
    readonly_fields = ("api_key", "supabase_user_id", "created_at")


@admin.register(MonthlyUsage)
class MonthlyUsageAdmin(admin.ModelAdmin):
    list_display = ("subject", "month", "count")
    search_fields = ("subject",)
