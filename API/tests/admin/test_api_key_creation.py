import json

import pytest
from rest_framework.test import APIClient, APIRequestFactory

from API.models import APIUserKey
from API.permissions import HasAPIKeyCreationPermission, HasValidAPIKey

params = [
    ("no-key", ""),
    ("fail", "test-key-wo-permission"),
    ("success", "test-key-with-permission"),
]


@pytest.fixture(params=params, ids=[p[0] for p in params])
def requests(request):
    test, key_name = request.param
    factory = APIRequestFactory()

    if test == "success":
        api_key, key = APIUserKey.objects.create_key(name=key_name, can_create_key=True)
    elif test == "fail":
        api_key, key = APIUserKey.objects.create_key(
            name=key_name, can_create_key=False
        )
    else:
        key = ""

    data = {"name": f"api-key-test-{test}", "username": "", "email": ""}
    request_obj = factory.post("/api/v1/create-key/", data, format="json")
    if key:
        request_obj.META["HTTP_X_API_KEY"] = key

    if request.node.name == "test_valid_key_permission[fail]":
        api_key.revoke()

    yield request_obj, test == "success"


@pytest.mark.django_db
class TestApiKeyCreation:
    def setup_method(self, method):
        self.url = "/api/v1/create-key/"
        self.client = APIClient()

    @pytest.mark.django_db
    def test_valid_key_permission(self, requests):
        permission = HasValidAPIKey()
        request_obj, expected = requests
        assert permission.has_permission(request_obj, None) == expected

    @pytest.mark.django_db
    def test_api_key_creation_permission(self, requests):
        permission = HasAPIKeyCreationPermission()
        request_obj, expected = requests
        assert permission.has_permission(request_obj, None) == expected

    @pytest.mark.django_db
    def test_api_key_creation(self, requests):
        request_obj, expected = requests
        expected = 200 if expected else 403
        data = json.loads(request_obj.body)
        headers = dict(request_obj.headers)
        api_key = request_obj.META.get("HTTP_X_API_KEY")
        if api_key:
            headers |= {"HTTP_X_API_KEY": api_key}
        response = self.client.post(path=self.url, data=data, **headers)
        assert response.status_code == expected
