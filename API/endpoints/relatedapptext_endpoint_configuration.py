from rest_framework import serializers

from API.documents.APIResponse import APIResponseDocument
from API.PVAPIViews import PVAPIListView
from API.serializers.APISubdocSerializer import PVAPIDocumentSerializer


class RelatedTextSerializer(PVAPIDocumentSerializer):
    # the granted/pre-grant fields will be accepted by either endpoint (i.e. not throw an Invalid Field error)
    # but only the relevant fields will be returned
    uuid = serializers.CharField(max_length=32, required=False)  # granted
    id = serializers.CharField(max_length=32, required=False)  # pre-grant
    patent_id = serializers.CharField(max_length=32, required=False)  # granted
    document_number = serializers.IntegerField(required=False)  # pre-grant
    related_text = serializers.CharField(max_length=1_000_000, required=False)  # both


# Defines the wrapper for a list of granted related application text documents
class RelatedTextResponseDocument(APIResponseDocument):
    def __init__(self, error, count, total_hits, rel_app_text):
        super(RelatedTextResponseDocument, self).__init__(error, count, total_hits)
        self.rel_app_texts = rel_app_text


# Defines the wrapper for a list of pre-grant related application text documents
class RelatedTextPubResponseDocument(APIResponseDocument):
    def __init__(self, error, count, total_hits, rel_app_text_publications):
        super(RelatedTextPubResponseDocument, self).__init__(error, count, total_hits)
        self.rel_app_texts = rel_app_text_publications


# Fields, operators, elastic search and other configuration for granted related text endpoint
class RelatedTextEndpoint:
    def __init__(self, **kwargs):
        # Elasticsearch Index
        self.index = "rel_app_text"
        # Fields which are configured as "text" type in ES and consequently require ".keyword" suffix for keyword like operation
        self.keyword_field_translations = []
        # Default f and s fields
        self.f = ["patent_id", "related_text"]
        self.o = {"pad_patent_id": False}
        self.s = [{"patent_id": "asc"}]
        # List of all allowed fields
        self.field_list = list(
            RelatedTextSerializer.__dict__["_declared_fields"].keys()
        )
        # Wrapper that defines the format for API response
        self.response_encoder = RelatedTextResponseDocument


# Fields, operators, elastic search and other configuration for pre-grant related text endpoint
class RelatedTextPubEndpoint:
    def __init__(self, **kwargs):
        # Elasticsearch Index
        self.index = "rel_app_text_publications"
        # Fields which are configured as "text" type in ES and consequently require ".keyword" suffix for keyword like operation
        self.keyword_field_translations = []
        # Default f and s fields
        self.f = ["document_number", "related_text"]
        self.s = [{"document_number": "asc"}]
        # List of all allowed fields
        self.field_list = list(
            RelatedTextSerializer.__dict__["_declared_fields"].keys()
        )
        # Wrapper that defines the format for API response
        self.response_encoder = RelatedTextPubResponseDocument


# DRF View for showing multiple granted related text documents
class RelatedTextList(PVAPIListView, RelatedTextEndpoint):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        RelatedTextEndpoint.__init__(self)


# DRF View for showing multiple pre-grant related text documents
class RelatedTextPubList(PVAPIListView, RelatedTextPubEndpoint):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        RelatedTextPubEndpoint.__init__(self)
