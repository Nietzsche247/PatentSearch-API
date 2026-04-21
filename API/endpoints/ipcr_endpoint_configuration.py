from rest_framework import serializers

from API.documents.APIResponse import APIResponseDocument
from API.PVAPIViews import PVAPIListView
from API.serializers.APISubdocSerializer import PVAPIDocumentSerializer


class IPCRSerializer(PVAPIDocumentSerializer):
    ipc_id = serializers.CharField(max_length=32, required=True)
    ipc_section = serializers.CharField(max_length=32, required=True)
    ipc_class = serializers.CharField(max_length=32, required=True)
    ipc_subclass = serializers.CharField(max_length=32, required=True)


class IPCRResponseDocument(APIResponseDocument):
    def __init__(self, error, count, total_hits, ipcr):
        super(IPCRResponseDocument, self).__init__(error, count, total_hits)
        self.ipcr = ipcr


class IPCREndpoint:
    def __init__(self, **kwargs):
        self.index = "ipcr"
        self.f = list(IPCRSerializer.__dict__["_declared_fields"].keys())
        self.s = [{"ipc_id": "asc"}]
        self.field_list = list(IPCRSerializer.__dict__["_declared_fields"].keys())
        self.response_encoder = IPCRResponseDocument
        self.q = {}
        self.lookup = True


class IPCRList(PVAPIListView, IPCREndpoint):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        IPCREndpoint.__init__(self)
