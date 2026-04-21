from rest_framework import serializers

from API.documents.APIResponse import APIResponseDocument
from API.PVAPIViews import PVAPIDetailView, PVAPIListView
from API.serializers.APISubdocSerializer import PVAPIDocumentSerializer


class PersonsSerializer(PVAPIDocumentSerializer):
    id = serializers.CharField(max_length=32)
    first_name = serializers.CharField(max_length=32, required=False)
    last_name = serializers.CharField(max_length=32, required=False)
    gender = serializers.CharField(max_length=32, required=False)


# Defines the wrapper for a list of response documents
class PersonsResponseDocument(APIResponseDocument):
    def __init__(self, error, count, total_hits, toy_index):
        super(PersonsResponseDocument, self).__init__(error, count, total_hits)
        self.toy_persons = toy_index  # match response key in openapi json and the variable name in the API Serializer


# Fields, operators, elastic search and other configuration for assignee endpoint
class PersonsEndpoint:
    def __init__(self, **kwargs):
        # Elasticsearch Index
        self.index = "toy_index"  # needs to match ES index
        # Fields which are configured as "text" type in ES and consequently require ".keyword" suffix for keyword
        # like operation
        self.keyword_field_translations = [
            # "first_name",
            # "last_name",
            # "gender"
        ]
        # Default f and s fields
        self.f = [
            "id",
            "first_name",
            "last_name",
            "gender",
        ]
        self.s = [{"id": "asc"}]
        # List of all allowed fields
        self.field_list = list(PersonsSerializer.__dict__["_declared_fields"].keys())
        # Wrapper that defines the format for API response
        self.response_encoder = PersonsResponseDocument


###############################################################


# DRF View for showing multiple assignees
class PersonsList(PVAPIListView, PersonsEndpoint):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        PersonsEndpoint.__init__(self)


# DRF View for showing single assignee
class PersonsDetail(PVAPIDetailView, PersonsEndpoint):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        PersonsEndpoint.__init__(self)
        self.pk_field = "id"
