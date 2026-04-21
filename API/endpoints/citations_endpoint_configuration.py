from rest_framework import serializers

from API.documents.APIResponse import APIResponseDocument
from API.PVAPIViews import PVAPIDetailView, PVAPIListView
from API.serializers.APISubdocSerializer import PVAPIDocumentSerializer
from API.serializers.custom_serializers_fields import IDHyperlinker


class USApplicationCitationSerializer(PVAPIDocumentSerializer):
    patent_id = serializers.CharField(max_length=32, required=False)
    patent = IDHyperlinker(read_only=True, view_name="patent-detail")
    citation_sequence = serializers.IntegerField(required=False)
    citation_document_number = serializers.CharField(max_length=32, required=False)
    citation_wipo_kind = serializers.CharField(max_length=32, required=False)
    citation_category = serializers.CharField(max_length=32, required=False)
    citation_date = serializers.CharField(max_length=32, required=False)
    citation_name = serializers.CharField(max_length=32, required=False)


class USPatentCitationSerializer(PVAPIDocumentSerializer):
    patent_id = serializers.CharField(max_length=32, required=False)
    patent = IDHyperlinker(read_only=True, view_name="patent-detail")
    citation_sequence = serializers.IntegerField(required=False)
    citation_patent_id = serializers.CharField(max_length=32, required=False)
    citation_patent = IDHyperlinker(read_only=True, view_name="patent-detail")
    citation_wipo_kind = serializers.CharField(max_length=32, required=False)
    citation_category = serializers.CharField(max_length=32, required=False)
    citation_date = serializers.DateField(required=False)
    citation_name = serializers.CharField(max_length=32, required=False)


class USPatentCitationResponseDocument(APIResponseDocument):
    def __init__(self, error, count, total_hits, us_patent_citations):
        super(USPatentCitationResponseDocument, self).__init__(error, count, total_hits)
        self.us_patent_citations = us_patent_citations


class USApplicationCitationResponseDocument(APIResponseDocument):
    def __init__(self, error, count, total_hits, us_application_citations):
        super(USApplicationCitationResponseDocument, self).__init__(
            error, count, total_hits
        )
        self.us_application_citations = us_application_citations


class USPatentCitationEndpoint:
    def __init__(self, **kwargs):
        self.index = "us_patent_citations"
        self.f = ["patent_id", "citation_patent_id", "citation_date"]
        self.o = {"pad_patent_id": False}
        self.s = [{"patent_id": "asc"}]
        self.field_list = list(
            USPatentCitationSerializer.__dict__["_declared_fields"].keys()
        )
        self.response_encoder = USPatentCitationResponseDocument


class USApplicationCitationEndpoint:
    def __init__(self, **kwargs):
        self.index = "us_application_citations"
        self.f = ["patent_id", "citation_document_number", "citation_sequence"]
        self.o = {"pad_patent_id": False}
        self.s = [{"patent_id": "asc"}]
        self.field_list = list(
            USApplicationCitationSerializer.__dict__["_declared_fields"].keys()
        )

        self.response_encoder = USApplicationCitationResponseDocument


class USPatentCitationList(PVAPIListView, USPatentCitationEndpoint):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        USPatentCitationEndpoint.__init__(self)


class USPatentCitationDetail(PVAPIDetailView, USPatentCitationEndpoint):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        USPatentCitationEndpoint.__init__(self)
        self.pk_field = "patent_id"
        self.lookup = True


class USApplicationCitationList(PVAPIListView, USApplicationCitationEndpoint):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        USApplicationCitationEndpoint.__init__(self)


class USApplicationCitationDetail(PVAPIDetailView, USApplicationCitationEndpoint):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        USApplicationCitationEndpoint.__init__(self)
        self.pk_field = "patent_id"
        self.lookup = True
