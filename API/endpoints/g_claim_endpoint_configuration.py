from rest_framework import serializers

from API.documents.APIResponse import APIResponseDocument
from API.PVAPIViews import PVAPIListView
from API.serializers.APISubdocSerializer import PVAPIDocumentSerializer


class GClaimSerializer(PVAPIDocumentSerializer):
    patent_id = serializers.CharField(max_length=32, required=False)
    claim_number = serializers.CharField(max_length=32, required=False)
    claim_dependent = serializers.CharField(max_length=256, required=False)
    claim_text = serializers.CharField(max_length=262_144, required=False)
    claim_sequence = serializers.IntegerField(required=False)
    exemplary = serializers.CharField(max_length=32, required=False)


class GClaimResponseDocument(APIResponseDocument):
    def __init__(self, error, count, total_hits, g_claims):
        super(GClaimResponseDocument, self).__init__(error, count, total_hits)
        self.g_claims = g_claims


class GClaimEndpoint:
    def __init__(self, **kwargs):
        # Elasticsearch Index
        self.index = "g_claims"
        # Fields which are configured as "text" type in ES and consequently require ".keyword" suffix for keyword like operation
        self.keyword_field_translations = []
        # Default f and s fields
        self.f = ["patent_id", "claim_sequence", "claim_text"]
        self.o = {"pad_patent_id": False}
        self.s = [{"patent_id": "asc"}, {"claim_sequence": "asc"}]
        # List of all allowed fields
        self.field_list = list(GClaimSerializer.__dict__["_declared_fields"].keys())
        # Wrapper that defines the format for API response
        self.response_encoder = GClaimResponseDocument


class GClaimList(PVAPIListView, GClaimEndpoint):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        GClaimEndpoint.__init__(self)
