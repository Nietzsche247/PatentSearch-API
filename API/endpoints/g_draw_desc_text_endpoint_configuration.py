from rest_framework import serializers

from API.documents.APIResponse import APIResponseDocument
from API.PVAPIViews import PVAPIListView
from API.serializers.APISubdocSerializer import PVAPIDocumentSerializer


class GDrawDescTextSerializer(PVAPIDocumentSerializer):
    patent_id = serializers.CharField(max_length=32, required=False)
    draw_desc_text = serializers.CharField(max_length=262_144, required=False)
    draw_desc_sequence = serializers.IntegerField(required=False)


# Defines the wrapper for a list of foreigncitation documents
class GDrawDescTextResponseDocument(APIResponseDocument):
    def __init__(self, error, count, total_hits, g_draw_desc_texts):
        super(GDrawDescTextResponseDocument, self).__init__(error, count, total_hits)
        self.g_draw_desc_texts = g_draw_desc_texts


# Fields, operators, elastic search and other configuration for drawing description endpoint
class GDrawDescTextEndpoint:
    def __init__(self, **kwargs):
        # Elasticsearch Index
        self.index = "g_draw_desc_texts"
        # Fields which are configured as "text" type in ES and consequently require ".keyword" suffix for keyword like operation
        self.keyword_field_translations = []
        # Default f and s fields
        self.f = ["patent_id", "draw_desc_sequence", "draw_desc_text"]
        self.o = {"pad_patent_id": False}
        self.s = [{"patent_id": "asc"}, {"draw_desc_sequence": "asc"}]
        # List of all allowed fields
        self.field_list = list(
            GDrawDescTextSerializer.__dict__["_declared_fields"].keys()
        )
        # Wrapper that defines the format for API response
        self.response_encoder = GDrawDescTextResponseDocument


# DRF View for showing multiple description texts
class GDrawDescTextList(PVAPIListView, GDrawDescTextEndpoint):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        GDrawDescTextEndpoint.__init__(self)
