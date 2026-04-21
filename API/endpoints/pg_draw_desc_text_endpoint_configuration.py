from rest_framework import serializers

from API.documents.APIResponse import APIResponseDocument
from API.PVAPIViews import PVAPIListView
from API.serializers.APISubdocSerializer import PVAPIDocumentSerializer


class PGDrawDescTextSerializer(PVAPIDocumentSerializer):
    document_number = serializers.IntegerField(required=False)
    draw_desc_text = serializers.CharField(max_length=32, required=False)
    draw_desc_sequence = serializers.IntegerField(required=False)


# Defines the wrapper for a list of foreigncitation documents
class PGDrawDescTextResponseDocument(APIResponseDocument):
    def __init__(self, error, count, total_hits, pg_draw_desc_texts):
        super(PGDrawDescTextResponseDocument, self).__init__(error, count, total_hits)
        self.pg_draw_desc_texts = pg_draw_desc_texts


# Fields, operators, elastic search and other configuration for inventor endpoint
class PGDrawDescTextEndpoint:
    def __init__(self, **kwargs):
        # Elasticsearch Index
        self.index = "pg_draw_desc_texts"
        # Fields which are configured as "text" type in ES and consequently require ".keyword" suffix for keyword like operation
        self.keyword_field_translations = []
        # Default f and s fields
        self.f = ["document_number", "draw_desc_sequence", "draw_desc_text"]
        self.s = [{"document_number": "asc"}, {"draw_desc_sequence": "asc"}]
        # List of all allowed fields
        self.field_list = list(
            PGDrawDescTextSerializer.__dict__["_declared_fields"].keys()
        )
        # Wrapper that defines the format for API response
        self.response_encoder = PGDrawDescTextResponseDocument


# DRF View for showing multiple inventors
class PGDrawDescTextList(PVAPIListView, PGDrawDescTextEndpoint):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        PGDrawDescTextEndpoint.__init__(self)
