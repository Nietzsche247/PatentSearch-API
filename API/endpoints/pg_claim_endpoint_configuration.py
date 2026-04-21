from rest_framework import serializers

from API.documents.APIResponse import APIResponseDocument
from API.PVAPIViews import PVAPIListView
from API.serializers.APISubdocSerializer import PVAPIDocumentSerializer


class PGClaimSerializer(PVAPIDocumentSerializer):
    document_number = serializers.IntegerField(required=False)
    claim_number = serializers.CharField(max_length=32, required=False)
    claim_text = serializers.CharField(max_length=32, required=False)
    claim_sequence = serializers.IntegerField(required=False)
    claim_dependent = serializers.CharField(max_length=512, required=False)


class PGClaimResponseDocument(APIResponseDocument):
    def __init__(self, error, count, total_hits, pg_claims):
        super(PGClaimResponseDocument, self).__init__(error, count, total_hits)
        self.pg_claims = pg_claims


class PGClaimEndpoint:
    def __init__(self, **kwargs):
        # Elasticsearch Index
        self.index = "pg_claims"
        # Fields which are configured as "text" type in ES and consequently require ".keyword" suffix for keyword like operation
        self.keyword_field_translations = []
        # Default f and s fields
        self.f = ["document_number", "claim_sequence", "claim_text"]
        self.s = [{"document_number": "asc"}, {"claim_sequence": "asc"}]
        # List of all allowed fields
        self.field_list = list(PGClaimSerializer.__dict__["_declared_fields"].keys())
        # Wrapper that defines the format for API response
        self.response_encoder = PGClaimResponseDocument


class PGClaimList(PVAPIListView, PGClaimEndpoint):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        PGClaimEndpoint.__init__(self)
