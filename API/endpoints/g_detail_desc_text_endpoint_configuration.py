from rest_framework import serializers

from API.documents.APIResponse import APIResponseDocument
from API.PVAPIViews import PVAPIListView
from API.serializers.APISubdocSerializer import PVAPIDocumentSerializer


class GDetailDescTextSerializer(PVAPIDocumentSerializer):
    patent_id = serializers.CharField(max_length=32, required=False)
    description_text = serializers.CharField(max_length=4_194_304, required=False)
    description_length = serializers.IntegerField(required=False)


# Defines the wrapper for a list of description text documents
class GDetailDescTextResponseDocument(APIResponseDocument):
    def __init__(self, error, count, total_hits, g_detail_desc_texts):
        super(GDetailDescTextResponseDocument, self).__init__(error, count, total_hits)
        self.g_detail_desc_texts = g_detail_desc_texts


# Fields, operators, elastic search and other configuration for description text endpoint
class GDetailDescTextEndpoint:
    def __init__(self, **kwargs):
        # Elasticsearch Index
        self.index = "g_detail_desc_texts"
        # Fields which are configured as "text" type in ES and consequently require ".keyword" suffix for keyword like operation
        self.keyword_field_translations = []
        # Default f and s fields
        self.f = ["patent_id", "description_text"]
        self.o = {"pad_patent_id": False}
        self.s = [{"patent_id": "asc"}]
        # List of all allowed fields
        self.field_list = list(
            GDetailDescTextSerializer.__dict__["_declared_fields"].keys()
        )
        # Wrapper that defines the format for API response
        self.response_encoder = GDetailDescTextResponseDocument


# DRF View for showing multiple description texts
class GDetailDescTextList(PVAPIListView, GDetailDescTextEndpoint):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        GDetailDescTextEndpoint.__init__(self)
