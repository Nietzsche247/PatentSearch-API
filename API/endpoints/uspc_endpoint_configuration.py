from rest_framework import serializers

from API.documents.APIResponse import APIResponseDocument
from API.PVAPIViews import PVAPIDetailView, PVAPIListView
from API.serializers.APISubdocSerializer import PVAPIDocumentSerializer
from API.serializers.custom_serializers_fields import IDHyperlinker


class USPCMainclassLookupSerializer(PVAPIDocumentSerializer):
    uspc_mainclass_id = serializers.CharField(max_length=32, required=False)
    uspc_mainclass_title = serializers.CharField(max_length=1024, required=False)
    uspc_mainclass_first_seen_date = serializers.DateField(required=False)
    uspc_mainclass_last_seen_date = serializers.DateField(required=False)
    uspc_mainclass_years_active = serializers.IntegerField(required=False)
    uspc_mainclass_num_patents = serializers.IntegerField(required=False)
    uspc_mainclass_num_assignees = serializers.IntegerField(required=False)
    uspc_mainclass_num_inventors = serializers.IntegerField(required=False)


class USPCSubclassLookupSerializer(PVAPIDocumentSerializer):
    uspc_subclass_id = serializers.CharField(max_length=32, required=False)
    uspc_subclass_title = serializers.CharField(max_length=1024, required=False)
    uspc_mainclass_id = serializers.CharField(max_length=32, required=False)
    uspc_mainclass = IDHyperlinker(
        read_only=True, many=False, view_name="uspc_mainclass-detail"
    )
    # fields don't currently exist
    # uspc_subclass_first_seen_date = serializers.DateField(required=False)
    # uspc_subclass_last_seen_date = serializers.DateField(required=False)
    # uspc_subclass_years_active = serializers.IntegerField(required=False)
    # uspc_subclass_num_patents = serializers.IntegerField(required=False)
    # uspc_subclass_num_assignees = serializers.IntegerField(required=False)
    # uspc_subclass_num_inventors = serializers.IntegerField(required=False)


class USPCMainclassResponseDocument(APIResponseDocument):
    def __init__(self, error, count, total_hits, uspc_mainclasses):
        super(USPCMainclassResponseDocument, self).__init__(error, count, total_hits)
        self.uspc_mainclasses = uspc_mainclasses


class USPCSubclassResponseDocument(APIResponseDocument):
    def __init__(self, error, count, total_hits, uspc_subclasses):
        super().__init__(error, count, total_hits)
        self.uspc_subclasses = uspc_subclasses


class USPCMainclassEndpoint:
    def __init__(self):
        self.index = "uspc_mainclasses"
        self.f = ["uspc_mainclass_id", "uspc_mainclass_title"]
        self.s = [{"uspc_mainclass_id": "asc"}]
        self.field_list = list(
            USPCMainclassLookupSerializer.__dict__["_declared_fields"].keys()
        )
        self.response_encoder = USPCMainclassResponseDocument
        self.q = {}
        self.lookup = True
        self.keyword_field_translations = []


class USPCSubclassEndpoint:
    def __init__(self):
        self.index = "uspc_subclasses"
        self.f = ["uspc_subclass_id", "uspc_subclass_title"]
        self.s = [{"uspc_subclass_id": "asc"}]
        self.field_list = list(
            USPCSubclassLookupSerializer.__dict__["_declared_fields"].keys()
        )
        self.response_encoder = USPCSubclassResponseDocument
        self.response_remapper = []
        self.q = {}
        self.lookup = True
        self.keyword_field_translations = []


class USPCMainclassList(PVAPIListView, USPCMainclassEndpoint):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        USPCMainclassEndpoint.__init__(self)


class USPCMainclassDetail(PVAPIDetailView, USPCMainclassEndpoint):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        USPCMainclassEndpoint.__init__(self)
        self.pk_field = "uspc_mainclass_id"
        self.lookup = True


class USPCSubclassList(PVAPIListView, USPCSubclassEndpoint):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        USPCSubclassEndpoint.__init__(self)


class USPCSubclassDetail(PVAPIDetailView, USPCSubclassEndpoint):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        USPCSubclassEndpoint.__init__(self)
        self.pk_field = "uspc_subclass_id"

    def get(self, request, pk, format=None):
        prefix, suffix = pk.split(":")
        pk = "{prefix}/{suffix}".format(prefix=prefix, suffix=suffix)
        self.q = {self.pk_field: pk}
        return self.query_handler(request)
