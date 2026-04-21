from rest_framework import serializers

from API.documents.APIResponse import APIResponseDocument
from API.PVAPIViews import PVAPIListView
from API.serializers.APISubdocSerializer import PVAPIDocumentSerializer
from API.serializers.custom_serializers_fields import IDHyperlinker


class ForeignCitationSerializer(PVAPIDocumentSerializer):
    patent_id = serializers.CharField(max_length=32, required=False)
    patent = IDHyperlinker(read_only=True, view_name="patent-detail")
    citation_number = serializers.CharField(max_length=32, required=False)
    citation_sequence = serializers.IntegerField(required=False)
    citation_date = serializers.CharField(max_length=32, required=False)
    citation_category = serializers.CharField(max_length=32, required=False)
    citation_country = serializers.CharField(max_length=32, required=False)


# Defines the wrapper for a list of foreign citation documents
class ForeignCitationResponseDocument(APIResponseDocument):
    def __init__(self, error, count, total_hits, foreign_citations):
        super(ForeignCitationResponseDocument, self).__init__(error, count, total_hits)
        self.foreign_citations = foreign_citations


# Fields, operators, elastic search and other configuration for foreign citation endpoint
class ForeignCitationEndpoint:
    def __init__(self, **kwargs):
        # Elasticsearch Index
        self.index = "foreign_citations"
        # Fields which are configured as "text" type in ES and consequently require ".keyword" suffix for keyword like operation
        self.keyword_field_translations = []
        # Default f and s fields
        self.f = ["patent_id", "citation_sequence", "citation_number"]
        self.o = {"pad_patent_id": False}
        self.s = [{"patent_id": "asc", "citation_sequence": "asc"}]
        # List of all allowed fields
        self.field_list = list(
            ForeignCitationSerializer.__dict__["_declared_fields"].keys()
        )
        # Wrapper that defines the format for API response
        self.response_encoder = ForeignCitationResponseDocument


# DRF View for showing multiple foreign citations
class ForeignCitationList(PVAPIListView, ForeignCitationEndpoint):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        ForeignCitationEndpoint.__init__(self)
