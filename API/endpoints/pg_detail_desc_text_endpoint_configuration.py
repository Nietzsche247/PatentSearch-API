from rest_framework import serializers

from API.documents.APIResponse import APIResponseDocument
from API.PVAPIViews import PVAPIListView
from API.serializers.APISubdocSerializer import PVAPIDocumentSerializer


class PGDetailDescTextSerializer(PVAPIDocumentSerializer):
    document_number = serializers.IntegerField(required=False)
    description_text = serializers.CharField(max_length=32, required=False)
    description_length = serializers.IntegerField(required=False)


# Defines the wrapper for a list of foreigncitation documents
class PGDetailDescTextResponseDocument(APIResponseDocument):
    def __init__(self, error, count, total_hits, pg_detail_desc_texts):
        super(PGDetailDescTextResponseDocument, self).__init__(error, count, total_hits)
        self.pg_detail_desc_texts = pg_detail_desc_texts


# Fields, operators, elastic search and other configuration for inventor endpoint
class PGDetailDescTextEndpoint:
    def __init__(self, **kwargs):
        # Elasticsearch Index
        self.index = "pg_detail_desc_texts"
        # Fields which are configured as "text" type in ES and consequently require ".keyword" suffix for keyword like operation
        self.keyword_field_translations = []
        # Default f and s fields
        self.f = ["document_number", "description_text"]
        self.s = [{"document_number": "asc"}]
        # List of all allowed fields
        self.field_list = list(
            PGDetailDescTextSerializer.__dict__["_declared_fields"].keys()
        )
        # Wrapper that defines the format for API response
        self.response_encoder = PGDetailDescTextResponseDocument


# DRF View for showing multiple inventors
class PGDetailDescTextList(PVAPIListView, PGDetailDescTextEndpoint):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        PGDetailDescTextEndpoint.__init__(self)
