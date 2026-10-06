"""Account records for PatentRef (checklist gate 5.10).

Supabase Auth is the one identity system; Django never stores a password. An `AccountKey` links one
upstream `API.APIUserKey` record (the thing the `X-Api-Key` header is checked against, unchanged) to
the Supabase user that minted it. `MonthlyUsage` is the monthly counter behind the Free-tier cap: one
row per subject per UTC month, incremented by `lapse_accounts.middleware.MeteringMiddleware`.
"""
from django.db import models

PLAN_FREE = "free"
PLANS = [(PLAN_FREE, "Free")]


class AccountKey(models.Model):
    api_key = models.OneToOneField("API.APIUserKey", on_delete=models.CASCADE, related_name="account")
    supabase_user_id = models.CharField(max_length=64, db_index=True)
    email = models.EmailField()
    plan = models.CharField(max_length=32, choices=PLANS, default=PLAN_FREE)
    created_at = models.DateTimeField(auto_now_add=True)
    rotated_from = models.CharField(max_length=16, blank=True, default="", help_text="prefix of the key this one replaced")

    class Meta:
        indexes = [models.Index(fields=["supabase_user_id", "plan"])]

    def __str__(self):
        return f"{self.email} ({self.plan}, {self.api_key.prefix})"

    @property
    def prefix(self):
        return self.api_key.prefix

    @property
    def subject(self):
        return "user:" + self.supabase_user_id


class MonthlyUsage(models.Model):
    """Requests counted against one subject in one UTC calendar month.

    `subject` is `user:<supabase_user_id>` for keys minted from an account (so a rotated key keeps the
    month's count) and `key:<prefix>` for keys created any other way (the operator's own keys)."""

    subject = models.CharField(max_length=80)
    month = models.CharField(max_length=7, help_text="YYYY-MM, UTC")
    count = models.PositiveIntegerField(default=0)

    class Meta:
        unique_together = [("subject", "month")]

    def __str__(self):
        return f"{self.subject} {self.month}: {self.count}"
