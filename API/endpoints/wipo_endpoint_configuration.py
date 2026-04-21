from rest_framework import serializers

from API.documents.APIResponse import APIResponseDocument
from API.PVAPIViews import PVAPIDetailView, PVAPIListView
from API.serializers.APISubdocSerializer import PVAPIDocumentSerializer


class WIPOSerializer(PVAPIDocumentSerializer):
    wipo_id = serializers.CharField(max_length=32, required=False)
    sector_title = serializers.CharField(max_length=32, required=False)
    field_title = serializers.CharField(max_length=32, required=False)


class WIPOResponseDocument(APIResponseDocument):
    def __init__(self, error, count, total_hits, wipo):
        super(WIPOResponseDocument, self).__init__(error, count, total_hits)
        self.wipo = wipo


class WIPOEndpoint:
    def __init__(self, **kwargs):
        self.index = "wipo"
        self.f = list(WIPOSerializer.__dict__["_declared_fields"].keys())
        self.s = [{"wipo_id": "asc"}]
        self.field_list = list(WIPOSerializer.__dict__["_declared_fields"].keys())
        self.response_encoder = WIPOResponseDocument
        self.q = {}
        self.lookup = True
        self.keyword_field_translations = []


class WIPOList(PVAPIListView, WIPOEndpoint):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        WIPOEndpoint.__init__(self)


class WIPODetail(PVAPIDetailView, WIPOEndpoint):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        WIPOEndpoint.__init__(self)
        self.pk_field = "wipo_id"
        self.lookup = True
