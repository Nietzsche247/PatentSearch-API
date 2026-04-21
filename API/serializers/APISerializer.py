from rest_framework import serializers

from API.endpoints.assignee_endpoint_configuration import AssigneeSerializer
from API.endpoints.attorney_endpoint_configuration import AttorneySerializer
from API.endpoints.citations_endpoint_configuration import (
    USApplicationCitationSerializer,
    USPatentCitationSerializer,
)
from API.endpoints.cpc_endpoint_configuration import (
    CPCClassSerializer,
    CPCGroupLookupSerializer,
    CPCSubClassLookupSerializer,
)
from API.endpoints.foriegncitation_endpoint_configuration import (
    ForeignCitationSerializer,
)
from API.endpoints.g_brf_sum_text_endpoint_configuration import GBrfSumTextSerializer
from API.endpoints.g_claim_endpoint_configuration import GClaimSerializer
from API.endpoints.g_detail_desc_text_endpoint_configuration import (
    GDetailDescTextSerializer,
)
from API.endpoints.g_draw_desc_text_endpoint_configuration import (
    GDrawDescTextSerializer,
)
from API.endpoints.inventor_endpoint_configuration import InventorSerializer
from API.endpoints.ipcr_endpoint_configuration import IPCRSerializer
from API.endpoints.location_endpoint_configuration import LocationSerializer
from API.endpoints.nber_endpoint_configuration import (
    NBERCategoryLookupSerializer,
    NBERSubcategoryLookupSerializer,
)
from API.endpoints.otherref_endpoint_configuration import OtherReferenceSerializer
from API.endpoints.patent_endpoint_configuration import PatentSerializer
from API.endpoints.pg_brf_sum_text_endpoint_configuration import PGBrfSumTextSerializer
from API.endpoints.pg_claim_endpoint_configuration import PGClaimSerializer
from API.endpoints.pg_detail_desc_text_endpoint_configuration import (
    PGDetailDescTextSerializer,
)
from API.endpoints.pg_draw_desc_text_endpoint_configuration import (
    PGDrawDescTextSerializer,
)
from API.endpoints.publication_endpoint_configuration import PublicationSerializer
from API.endpoints.relatedapptext_endpoint_configuration import (  # note this serializer applies to both the granted and pre-grant endpoints
    RelatedTextSerializer,
)
from API.endpoints.uspc_endpoint_configuration import (
    USPCMainclassLookupSerializer,
    USPCSubclassLookupSerializer,
)
from API.endpoints.wipo_endpoint_configuration import WIPOSerializer


class APISerializer(serializers.Serializer):
    def create(self, validated_data):
        raise Exception("")

    def update(self, instance, validated_data):
        raise Exception("")

    # class properties/attributes
    error = serializers.BooleanField()
    count = serializers.IntegerField()
    total_hits = serializers.IntegerField()
    us_patent_citations = USPatentCitationSerializer(many=True, required=False)
    us_application_citations = USApplicationCitationSerializer(
        many=True, required=False
    )
    cpc_classes = CPCClassSerializer(many=True, required=False)
    cpc_subclasses = CPCSubClassLookupSerializer(many=True, required=False)
    cpc_groups = CPCGroupLookupSerializer(many=True, read_only=True, required=False)
    uspc_mainclasses = USPCMainclassLookupSerializer(
        many=True, read_only=True, required=False
    )
    uspc_subclasses = USPCSubclassLookupSerializer(
        many=True, read_only=True, required=False
    )
    nber_categories = NBERCategoryLookupSerializer(
        many=True, read_only=True, required=False
    )
    nber_subcategories = NBERSubcategoryLookupSerializer(
        many=True, read_only=True, required=False
    )
    patents = PatentSerializer(many=True, required=False)
    publications = PublicationSerializer(many=True, required=False)
    attorneys = AttorneySerializer(many=True, required=False)
    foreign_citations = ForeignCitationSerializer(many=True, required=False)
    assignees = AssigneeSerializer(many=True, required=False)
    inventors = InventorSerializer(many=True, required=False)
    locations = LocationSerializer(many=True, required=False)
    ipcr = IPCRSerializer(many=True, required=False)
    other_references = OtherReferenceSerializer(many=True, required=False)
    rel_app_texts = RelatedTextSerializer(many=True, required=False)
    wipo = WIPOSerializer(many=True, required=False)
    g_brf_sum_texts = GBrfSumTextSerializer(many=True, required=False)
    g_claims = GClaimSerializer(many=True, required=False)
    g_detail_desc_texts = GDetailDescTextSerializer(many=True, required=False)
    g_draw_desc_texts = GDrawDescTextSerializer(many=True, required=False)
    pg_brf_sum_texts = PGBrfSumTextSerializer(many=True, required=False)
    pg_claims = PGClaimSerializer(many=True, required=False)
    pg_detail_desc_texts = PGDetailDescTextSerializer(many=True, required=False)
    pg_draw_desc_texts = PGDrawDescTextSerializer(many=True, required=False)
