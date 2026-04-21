from rest_framework import serializers

from API.documents.APIResponse import APIResponseDocument
from API.PVAPIViews import PVAPIDetailView, PVAPIListView
from API.serializers.APISubdocSerializer import PVAPIDocumentSerializer
from API.serializers.custom_serializers_fields import IDHyperlinker, NumericField
from API.serializers.reusable_serializers import YearSerializer


class AssigneeSerializer(PVAPIDocumentSerializer):
    assignee_id = serializers.CharField(max_length=32, required=False)
    assignee_individual_name_first = serializers.CharField(
        max_length=32, required=False
    )
    assignee_individual_name_last = serializers.CharField(max_length=32, required=False)
    assignee_organization = serializers.CharField(max_length=32, required=False)
    assignee_type = serializers.CharField(max_length=32, required=False)
    assignee_lastknown_city = serializers.CharField(max_length=32, required=False)
    assignee_lastknown_state = serializers.CharField(max_length=32, required=False)
    assignee_lastknown_country = serializers.CharField(max_length=32, required=False)
    assignee_lastknown_latitude = serializers.FloatField(required=False)
    assignee_lastknown_longitude = serializers.FloatField(required=False)
    assignee_lastknown_location = IDHyperlinker(
        view_name="location-detail", read_only=True, many=False
    )
    assignee_first_seen_date = serializers.DateField(required=False)
    assignee_last_seen_date = serializers.DateField(required=False)
    assignee_num_inventors = NumericField(required=False)
    assignee_num_patents = NumericField(required=False)
    assignee_years_active = NumericField(required=False)
    assignee_years = serializers.ListField(child=YearSerializer(), required=False)


# Defines the wrapper for a list of assignee documents
class AssigneeResponseDocument(APIResponseDocument):
    def __init__(self, error, count, total_hits, assignees):
        super(AssigneeResponseDocument, self).__init__(error, count, total_hits)
        self.assignees = assignees


# Fields, operators, elastic search and other configuration for assignee endpoint
class AssigneeEndpoint:
    def __init__(self, **kwargs):
        # Elasticsearch Index
        self.index = "assignees"
        self.nested_paths = {"assignee_years": "assignee_years"}
        # Fields which are configured as "text" type in ES and consequently require ".keyword" suffix for keyword
        # like operation
        self.keyword_field_translations = [
            "assignee_individual_name_first",
            "assignee_individual_name_last",
            "assignee_organization",
            "lastknown_city",
            "lastknown_state",
            "lastknown_country",
        ]
        # Default f and s fields
        self.f = [
            "assignee_id",
            "assignee_individual_name_first",
            "assignee_individual_name_last",
            "assignee_organization",
        ]
        self.s = [{"assignee_id": "asc"}]
        # List of all allowed fields
        self.field_list = list(AssigneeSerializer.__dict__["_declared_fields"].keys())
        # Wrapper that defines the format for API response
        self.response_encoder = AssigneeResponseDocument


# DRF View for showing multiple assignees
class AssigneeList(PVAPIListView, AssigneeEndpoint):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        AssigneeEndpoint.__init__(self)


# DRF View for showing single assignee
class AssigneeDetail(PVAPIDetailView, AssigneeEndpoint):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        AssigneeEndpoint.__init__(self)
        self.pk_field = "assignee_id"
