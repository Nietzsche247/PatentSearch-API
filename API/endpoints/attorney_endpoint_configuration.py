from rest_framework import serializers

from API.documents.APIResponse import APIResponseDocument
from API.PVAPIViews import PVAPIDetailView, PVAPIListView
from API.serializers.APISubdocSerializer import PVAPIDocumentSerializer
from API.serializers.custom_serializers_fields import NumericField


class AttorneySerializer(PVAPIDocumentSerializer):
    attorney_id = serializers.CharField(max_length=32, required=False)
    attorney_name_first = serializers.CharField(max_length=32, required=False)
    attorney_name_last = serializers.CharField(max_length=32, required=False)
    attorney_organization = serializers.CharField(max_length=32, required=False)
    attorney_first_seen_date = serializers.DateField(required=False)
    attorney_last_seen_date = serializers.DateField(required=False)
    attorney_num_inventors = NumericField(required=False)
    attorney_num_patents = NumericField(required=False)
    attorney_years_active = serializers.FloatField(required=False)


# Defines the wrapper for a list of attorney documents
class AttorneyResponseDocument(APIResponseDocument):
    def __init__(self, error, count, total_hits, attorneys):
        super(AttorneyResponseDocument, self).__init__(error, count, total_hits)
        self.attorneys = attorneys


# Fields, operators, elastic search and other configuration for attorney endpoint
class AttorneyEndpoint:
    def __init__(self, **kwargs):
        # Elasticsearch Index
        self.index = "attorneys"
        # Fields which are configured as "text" type in ES and consequently require ".keyword" suffix for keyword like operation
        self.keyword_field_translations = ["attorney_organization"]
        # Default f and s fields
        self.f = [
            "attorney_id",
            "attorney_name_first",
            "attorney_name_last",
            "attorney_organization",
        ]
        self.s = [{"attorney_id": "asc"}]
        # List of all allowed fields
        self.field_list = list(AttorneySerializer.__dict__["_declared_fields"].keys())
        # Wrapper that defines the format for API response
        self.response_encoder = AttorneyResponseDocument


# DRF View for showing multiple attorneys
class AttorneyList(PVAPIListView, AttorneyEndpoint):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        AttorneyEndpoint.__init__(self)


# DRF View for showing single attorney
class AttorneyDetail(PVAPIDetailView, AttorneyEndpoint):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        AttorneyEndpoint.__init__(self)
        self.pk_field = "attorney_id"
