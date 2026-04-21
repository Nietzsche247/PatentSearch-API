from rest_framework import serializers

from API.documents.APIResponse import APIResponseDocument
from API.PVAPIViews import PVAPIListView
from API.serializers.APISubdocSerializer import PVAPIDocumentSerializer


class GBrfSumTextSerializer(PVAPIDocumentSerializer):
    patent_id = serializers.CharField(max_length=32, required=False)
    summary_text = serializers.CharField(max_length=524_288, required=False)


# Defines the wrapper for a list of foreigncitation documents
class GBrfSumTextResponseDocument(APIResponseDocument):
    def __init__(self, error, count, total_hits, g_brf_sum_texts):
        super(GBrfSumTextResponseDocument, self).__init__(error, count, total_hits)
        self.g_brf_sum_texts = g_brf_sum_texts


# Fields, operators, elastic search and other configuration for summary text endpoint
class GBrfSumTextEndpoint:
    def __init__(self, **kwargs):
        # Elasticsearch Index
        self.index = "g_brf_sum_texts"
        # Fields which are configured as "text" type in ES and consequently require ".keyword" suffix for keyword like operation
        self.keyword_field_translations = []
        # Default f and s fields
        self.f = ["patent_id", "summary_text"]
        self.o = {"pad_patent_id": False}
        self.s = [{"patent_id": "asc"}]
        # List of all allowed fields
        self.field_list = list(
            GBrfSumTextSerializer.__dict__["_declared_fields"].keys()
        )
        # Wrapper that defines the format for API response
        self.response_encoder = GBrfSumTextResponseDocument


# DRF View for showing multiple texts
class GBrfSumTextList(PVAPIListView, GBrfSumTextEndpoint):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        GBrfSumTextEndpoint.__init__(self)
