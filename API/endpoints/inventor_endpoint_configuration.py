from rest_framework import serializers

from API.documents.APIResponse import APIResponseDocument
from API.PVAPIViews import PVAPIDetailView, PVAPIListView
from API.serializers.APISubdocSerializer import PVAPIDocumentSerializer
from API.serializers.custom_serializers_fields import IDHyperlinker, NumericField
from API.serializers.reusable_serializers import YearSerializer


class InventorSerializer(PVAPIDocumentSerializer):
    inventor_id = serializers.CharField(required=False)
    inventor_name_first = serializers.CharField(max_length=128, required=False)
    inventor_name_last = serializers.CharField(max_length=128, required=False)
    inventor_gender_code = serializers.CharField(max_length=16, required=False)
    inventor_lastknown_city = serializers.CharField(max_length=128, required=False)
    inventor_lastknown_state = serializers.CharField(max_length=128, required=False)
    inventor_lastknown_country = serializers.CharField(max_length=32, required=False)
    inventor_lastknown_latitude = serializers.FloatField(required=False)
    inventor_lastknown_longitude = serializers.FloatField(required=False)
    inventor_lastknown_location = IDHyperlinker(
        view_name="location-detail", read_only=True, many=False
    )
    inventor_num_patents = NumericField(required=False)
    inventor_num_assignees = NumericField(required=False)
    inventor_first_seen_date = serializers.DateField(required=False)
    inventor_last_seen_date = serializers.DateField(required=False)
    inventor_years_active = serializers.FloatField(required=False)
    inventor_years = serializers.ListField(child=YearSerializer(), required=False)


# Defines the wrapper for a list of inventor documents
class InventorResponseDocument(APIResponseDocument):
    def __init__(self, error, count, total_hits, inventors):
        super(InventorResponseDocument, self).__init__(error, count, total_hits)
        self.inventors = inventors


# Fields, operators, elastic search and other configuration for inventor endpoint
class InventorEndpoint:
    def __init__(self, **kwargs):
        # Elasticsearch Index
        self.index = "inventors"
        self.nested_paths = {"inventor_years": "inventor_years"}
        # Fields which are configured as "text" type in ES and consequently require ".keyword" suffix for keyword
        # like operation
        self.keyword_field_translations = [
            "inventor_name_first",
            "inventor_name_last",
            "lastknown_city",
            "lastknown_state",
            "lastknown_country",
        ]
        # Default f and s fields
        self.f = ["inventor_id", "inventor_name_first", "inventor_name_last"]
        self.s = [{"inventor_id": "asc"}]
        # List of all allowed fields
        self.field_list = list(InventorSerializer.__dict__["_declared_fields"].keys())
        # Wrapper that defines the format for API response
        self.response_encoder = InventorResponseDocument


# DRF View for showing multiple inventors
class InventorList(PVAPIListView, InventorEndpoint):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        InventorEndpoint.__init__(self)


# DRF View for showing single inventor
class InventorDetail(PVAPIDetailView, InventorEndpoint):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        InventorEndpoint.__init__(self)
        self.pk_field = "inventor_id"
