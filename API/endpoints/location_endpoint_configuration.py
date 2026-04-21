from rest_framework import serializers

from API.documents.APIResponse import APIResponseDocument
from API.PVAPIViews import PVAPIDetailView, PVAPIListView
from API.serializers.APISubdocSerializer import PVAPIDocumentSerializer
from API.serializers.custom_serializers_fields import NumericField


# Defines the fields in a single location document and their types
class LocationSerializer(PVAPIDocumentSerializer):
    location_id = serializers.CharField(max_length=32, required=False)
    location_name = serializers.CharField(max_length=32, required=False)
    location_county = serializers.CharField(max_length=32, required=False)
    location_county_fips = serializers.CharField(max_length=32, required=False)
    location_state = serializers.CharField(max_length=32, required=False)
    location_state_fips = serializers.CharField(max_length=32, required=False)
    location_country = serializers.CharField(max_length=32, required=False)
    location_place_type = serializers.CharField(max_length=16, required=False)
    location_latitude = serializers.FloatField(required=False)
    location_longitude = serializers.FloatField(required=False)
    location_num_assignees = NumericField(required=False)
    location_num_patents = NumericField(required=False)
    location_num_inventors = NumericField(required=False)


# Defines the wrapper for a list of location documents
class LocationResponseDocument(APIResponseDocument):
    def __init__(self, error, count, total_hits, locations):
        super(LocationResponseDocument, self).__init__(error, count, total_hits)
        self.locations = locations


# Fields, operators, elastic search and other configuration for location endpoint
class LocationEndpoint:
    def __init__(self, **kwargs):
        # Elasticsearch Index
        self.index = "locations"
        # Fields which are configured as "text" type in ES and consequently require ".keyword" suffix for keyword like operation
        self.keyword_field_translations = [
            "location_name",
            "location_county",
            "location_state",
            "location_country",
        ]
        # Default f and s fields
        self.f = ["location_id", "location_name", "location_state", "location_country"]
        self.s = [{"location_id": "asc"}]
        # List of all allowed fields
        self.field_list = list(LocationSerializer.__dict__["_declared_fields"].keys())
        # Wrapper that defines the format for API response
        self.response_encoder = LocationResponseDocument


# DRF View for showing multiple locations
class LocationList(PVAPIListView, LocationEndpoint):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        LocationEndpoint.__init__(self)


# DRF View for showing single locations
class LocationDetail(PVAPIDetailView, LocationEndpoint):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        LocationEndpoint.__init__(self)
        self.pk_field = "location_id"
