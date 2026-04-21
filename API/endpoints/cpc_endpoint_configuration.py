from rest_framework import serializers

from API.documents.APIResponse import APIResponseDocument
from API.PVAPIViews import PVAPIDetailView, PVAPIListView
from API.serializers.APISubdocSerializer import PVAPIDocumentSerializer
from API.serializers.custom_serializers_fields import IDHyperlinker


class CPCClassSerializer(PVAPIDocumentSerializer):
    cpc_class_id = serializers.CharField(max_length=32, required=False)
    cpc_class_title = serializers.CharField(max_length=1024, required=False)
    cpc_class_first_seen_date = serializers.DateField(required=False)
    cpc_class_last_seen_date = serializers.DateField(required=False)
    cpc_class_years_active = serializers.IntegerField(required=False)
    cpc_class_num_patents = serializers.IntegerField(required=False)
    cpc_class_num_assignees = serializers.IntegerField(required=False)
    cpc_class_num_inventors = serializers.IntegerField(required=False)


class CPCSubClassLookupSerializer(PVAPIDocumentSerializer):
    cpc_subclass_id = serializers.CharField(max_length=32, required=False)
    cpc_subclass_title = serializers.CharField(max_length=1024, required=False)
    cpc_class_id = serializers.CharField(max_length=32, required=False)
    cpc_class = IDHyperlinker(read_only=True, many=False, view_name="cpc_class-detail")
    cpc_subclass_first_seen_date = serializers.DateField(required=False)
    cpc_subclass_last_seen_date = serializers.DateField(required=False)
    cpc_subclass_years_active = serializers.IntegerField(required=False)
    cpc_subclass_num_patents = serializers.IntegerField(required=False)
    cpc_subclass_num_assignees = serializers.IntegerField(required=False)
    cpc_subclass_num_inventors = serializers.IntegerField(required=False)


class CPCGroupLookupSerializer(PVAPIDocumentSerializer):
    cpc_group_id = serializers.CharField(max_length=32, required=False)
    cpc_group_title = serializers.CharField(max_length=2048, required=False)
    cpc_class_id = serializers.CharField(max_length=32, required=False)
    cpc_class = IDHyperlinker(read_only=True, many=False, view_name="cpc_class-detail")
    cpc_subclass_id = serializers.CharField(max_length=32, required=False)
    cpc_subclass = IDHyperlinker(
        read_only=True, many=False, view_name="cpc_subclass-detail"
    )


class USCPCClassResponseDocument(APIResponseDocument):
    def __init__(self, error, count, total_hits, cpc_classes):
        super(USCPCClassResponseDocument, self).__init__(error, count, total_hits)
        self.cpc_classes = cpc_classes


class USCPCSubClassResponseDocument(APIResponseDocument):
    def __init__(self, error, count, total_hits, cpc_subclasses):
        super(USCPCSubClassResponseDocument, self).__init__(error, count, total_hits)
        self.cpc_subclasses = cpc_subclasses


class USCPCGroupResponseDocument(APIResponseDocument):
    def __init__(self, error, count, total_hits, cpc_groups):
        super(USCPCGroupResponseDocument, self).__init__(error, count, total_hits)
        self.cpc_groups = cpc_groups


class CPCClassEndpoint:
    def __init__(self, **kwargs):
        self.index = "cpc_classes"
        self.f = ["cpc_class_id", "cpc_class_title"]
        self.s = [{"cpc_class_id": "asc"}]
        self.field_list = list(CPCClassSerializer.__dict__["_declared_fields"].keys())
        self.response_encoder = USCPCClassResponseDocument
        self.q = {}
        self.lookup = True
        self.keyword_field_translations = []


class CPCSubClassEndpoint:
    def __init__(self):
        self.index = "cpc_subclasses"
        self.f = ["cpc_subclass_id", "cpc_subclass_title", "cpc_class_id"]
        self.s = [{"cpc_subclass_id": "asc"}]
        self.field_list = list(
            CPCSubClassLookupSerializer.__dict__["_declared_fields"].keys()
        )

        self.response_encoder = USCPCSubClassResponseDocument
        self.response_remapper = []
        self.q = {}
        self.lookup = True
        self.keyword_field_translations = []


class CPCGroupEndpoint:
    def __init__(self):
        self.index = "cpc_groups"
        self.f = ["cpc_group_id", "cpc_group_title", "cpc_class_id", "cpc_subclass_id"]
        self.s = [{"cpc_group_id": "asc"}]
        self.field_list = list(
            CPCGroupLookupSerializer.__dict__["_declared_fields"].keys()
        )

        self.response_encoder = USCPCGroupResponseDocument
        self.response_remapper = []
        self.q = {}
        self.lookup = True
        self.keyword_field_translations = []


class CPCClassList(PVAPIListView, CPCClassEndpoint):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        CPCClassEndpoint.__init__(self, **kwargs)


class CPCClassDetail(PVAPIDetailView, CPCClassEndpoint):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        CPCClassEndpoint.__init__(self, **kwargs)
        self.pk_field = "cpc_class_id"


class CPCSubClassList(PVAPIListView, CPCSubClassEndpoint):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        CPCSubClassEndpoint.__init__(self)


class CPCSubClassDetail(PVAPIDetailView, CPCSubClassEndpoint):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        CPCSubClassEndpoint.__init__(self)
        self.pk_field = "cpc_subclass_id"


class CPCGroupList(PVAPIListView, CPCGroupEndpoint):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        CPCGroupEndpoint.__init__(self)


class CPCGroupDetail(PVAPIDetailView, CPCGroupEndpoint):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        CPCGroupEndpoint.__init__(self)
        self.pk_field = "cpc_group_id"

    def get(self, request, pk, format=None):
        prefix, suffix = pk.split(":")
        pk = "{prefix}/{suffix}".format(prefix=prefix, suffix=suffix)
        self.q = {self.pk_field: pk}
        return self.query_handler(request)
