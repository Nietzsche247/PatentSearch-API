from django.db import models
from rest_framework_api_key.models import AbstractAPIKey


class APIUserKey(AbstractAPIKey):
    username = models.CharField(verbose_name="API User Name", max_length=512)
    email = models.EmailField(
        null=True,
    )
    can_create_key = models.BooleanField(default=False)

    def __str__(self):
        return "{name} ({email})".format(name=self.name, email=self.email)

    def revoke(self):
        self.revoked = True
        self.save()
