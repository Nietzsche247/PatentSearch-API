from rest_framework import serializers

from API.documents.APIResponse import APIResponseDocument
from API.PVAPIViews import PVAPIListView
from API.serializers.APISubdocSerializer import PVAPIDocumentSerializer


class OtherReferenceSerializer(PVAPIDocumentSerializer):
    uuid = serializers.CharField(max_length=32, required=False)
    patent_id = serializers.CharField(max_length=32, required=False)
    reference_sequence = serializers.IntegerField(required=False)
    reference_text = serializers.CharField(max_length=32, required=False)


# Defines the wrapper for a list of reference documents
class OtherReferenceResponseDocument(APIResponseDocument):
    def __init__(self, error, count, total_hits, other_references):
        super(OtherReferenceResponseDocument, self).__init__(error, count, total_hits)
        self.other_references = other_references


# Fields, operators, elastic search and other configuration for other reference endpoint
class OtherReferenceEndpoint:
    def __init__(self, **kwargs):
        # Elasticsearch Index
        self.index = "other_references"
        # Fields which are configured as "text" type in ES and consequently require ".keyword" suffix for keyword like operation
        self.keyword_field_translations = []
        # Default f and s fields
        self.f = ["patent_id", "reference_sequence", "reference_text"]
        self.o = {"pad_patent_id": False}
        self.s = [{"patent_id": "asc", "reference_sequence": "asc"}]
        # List of all allowed fields
        self.field_list = list(
            OtherReferenceSerializer.__dict__["_declared_fields"].keys()
        )
        # Wrapper that defines the format for API response
        self.response_encoder = OtherReferenceResponseDocument


# DRF View for showing multiple references
class OtherReferenceList(PVAPIListView, OtherReferenceEndpoint):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        OtherReferenceEndpoint.__init__(self)
