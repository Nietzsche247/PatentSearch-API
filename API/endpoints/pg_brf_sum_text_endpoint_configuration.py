from rest_framework import serializers

from API.documents.APIResponse import APIResponseDocument
from API.PVAPIViews import PVAPIListView
from API.serializers.APISubdocSerializer import PVAPIDocumentSerializer


class PGBrfSumTextSerializer(PVAPIDocumentSerializer):
    document_number = serializers.IntegerField(required=False)
    summary_text = serializers.CharField(max_length=32, required=False)


# Defines the wrapper for a list of foreigncitation documents
class PGBrfSumTextResponseDocument(APIResponseDocument):
    def __init__(self, error, count, total_hits, pg_brf_sum_texts):
        super(PGBrfSumTextResponseDocument, self).__init__(error, count, total_hits)
        self.pg_brf_sum_texts = pg_brf_sum_texts


# Fields, operators, elastic search and other configuration for inventor endpoint
class PGBrfSumTextEndpoint:
    def __init__(self, **kwargs):
        # Elasticsearch Index
        self.index = "pg_brf_sum_texts"
        # Fields which are configured as "text" type in ES and consequently require ".keyword" suffix for keyword like operation
        self.keyword_field_translations = []
        # Default f and s fields
        self.f = ["document_number", "summary_text"]
        self.s = [{"document_number": "asc"}]
        # List of all allowed fields
        self.field_list = list(
            PGBrfSumTextSerializer.__dict__["_declared_fields"].keys()
        )
        # Wrapper that defines the format for API response
        self.response_encoder = PGBrfSumTextResponseDocument


# DRF View for showing multiple inventors
class PGBrfSumTextList(PVAPIListView, PGBrfSumTextEndpoint):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        PGBrfSumTextEndpoint.__init__(self)
