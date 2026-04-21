from rest_framework import serializers

from API.documents.APIResponse import APIResponseDocument
from API.PVAPIViews import PVAPIDetailView, PVAPIListView
from API.serializers.APISubdocSerializer import PVAPIDocumentSerializer
from API.serializers.custom_serializers_fields import IDHyperlinker, NumericField


def generate_serializer(serializer_class, **kwargs):
    required = kwargs.pop("required", False)
    return serializer_class(**kwargs, required=required)


class AssigneeNestedSerializer(PVAPIDocumentSerializer):
    assignee = IDHyperlinker(read_only=True, many=False, view_name="assignee-detail")
    assignee_id = generate_serializer(serializers.CharField, max_length=128)
    assignee_type = generate_serializer(serializers.CharField, max_length=512)
    assignee_individual_name_first = generate_serializer(
        serializers.CharField, max_length=128
    )
    assignee_individual_name_last = generate_serializer(
        serializers.CharField, max_length=128
    )
    assignee_organization = generate_serializer(serializers.CharField, max_length=256)
    assignee_location_id = generate_serializer(serializers.CharField, max_length=128)
    assignee_city = generate_serializer(serializers.CharField, max_length=128)
    assignee_state = generate_serializer(serializers.CharField, max_length=128)
    assignee_country = generate_serializer(serializers.CharField, max_length=32)
    assignee_sequence = generate_serializer(serializers.IntegerField)


class InventorNestedSerializer(PVAPIDocumentSerializer):
    inventor = IDHyperlinker(read_only=True, many=False, view_name="inventor-detail")
    inventor_id = generate_serializer(serializers.CharField, max_length=128)
    inventor_name_first = generate_serializer(serializers.CharField, max_length=128)
    inventor_name_last = generate_serializer(serializers.CharField, max_length=128)
    inventor_gender_code = generate_serializer(serializers.CharField, max_length=8)
    inventor_location_id = generate_serializer(serializers.CharField, max_length=128)
    inventor_city = generate_serializer(serializers.CharField, max_length=128)
    inventor_state = generate_serializer(serializers.CharField, max_length=128)
    inventor_country = generate_serializer(serializers.CharField, max_length=32)
    inventor_sequence = generate_serializer(serializers.IntegerField)


class CPCNestedSerializer(PVAPIDocumentSerializer):
    cpc_sequence = NumericField(required=False)
    cpc_section_id = generate_serializer(serializers.CharField, max_length=4)
    cpc_class = IDHyperlinker(read_only=True, many=False, view_name="cpc_class-detail")
    cpc_class_id = generate_serializer(serializers.CharField, max_length=8)
    cpc_subclass = IDHyperlinker(
        read_only=True, many=False, view_name="cpc_subclass-detail"
    )
    cpc_subclass_id = generate_serializer(serializers.CharField, max_length=8)
    cpc_group = IDHyperlinker(read_only=True, many=False, view_name="cpc_group-detail")
    cpc_group_id = generate_serializer(serializers.CharField, max_length=16)
    action_date = generate_serializer(serializers.DateField)
    document_number = generate_serializer(serializers.IntegerField)


class WIPONestedSerializer(PVAPIDocumentSerializer):
    wipo_field_id = generate_serializer(serializers.CharField, max_length=32)
    wipo_field = generate_serializer(serializers.CharField, max_length=32)
    wipo_sector_title = generate_serializer(serializers.CharField, max_length=32)
    wipo_sequence = generate_serializer(serializers.IntegerField)


class USPCNestedSerializer(PVAPIDocumentSerializer):
    uspc_mainclass = IDHyperlinker(
        read_only=True, many=False, view_name="uspc_mainclass-detail"
    )
    uspc_mainclass_id = generate_serializer(serializers.CharField, max_length=32)
    uspc_subclass = IDHyperlinker(
        read_only=True, many=False, view_name="uspc_subclass-detail"
    )
    uspc_subclass_id = generate_serializer(serializers.CharField, max_length=32)
    uspc_sequence = generate_serializer(serializers.IntegerField)


class USRelatedDocumentNestedSerializer(PVAPIDocumentSerializer):
    related_doc_type = generate_serializer(serializers.CharField, max_length=32)
    related_doc_kind = generate_serializer(serializers.CharField, max_length=32)
    related_doc_number = generate_serializer(serializers.CharField, max_length=32)
    published_country = generate_serializer(serializers.CharField, max_length=32)
    related_doc_published_date = generate_serializer(serializers.DateField)
    related_doc_sequence = generate_serializer(serializers.IntegerField)


class USPartiesNestedSerializer(PVAPIDocumentSerializer):
    us_party_type = generate_serializer(serializers.CharField, max_length=32)
    us_party_designation = generate_serializer(serializers.CharField, max_length=32)
    location_id = generate_serializer(serializers.CharField, max_length=128)
    us_party_name_first = generate_serializer(serializers.CharField, max_length=128)
    us_party_name_last = generate_serializer(serializers.CharField, max_length=128)
    us_party_organization = generate_serializer(serializers.CharField, max_length=128)
    us_party_sequence = generate_serializer(serializers.IntegerField)
    applicant_authority = generate_serializer(serializers.CharField, max_length=128)


class PCTDataNestedSerializer(PVAPIDocumentSerializer):
    published_filed_date = generate_serializer(serializers.DateField)
    pct_371_date = generate_serializer(serializers.DateField)
    pct_102_date = generate_serializer(serializers.DateField)
    application_kind = generate_serializer(serializers.CharField, max_length=32)
    pct_doc_number = generate_serializer(serializers.CharField, max_length=32)
    pct_doc_type = generate_serializer(serializers.CharField, max_length=32)
    filed_country = generate_serializer(serializers.CharField, max_length=128)


class IPCRNestedSerializer(PVAPIDocumentSerializer):
    ipc_id = generate_serializer(serializers.CharField, max_length=128)
    ipc_sequence = generate_serializer(serializers.IntegerField)
    ipc_action_date = generate_serializer(serializers.DateField)
    ipc_section = generate_serializer(serializers.CharField, max_length=32)
    ipc_class = generate_serializer(serializers.CharField, max_length=32)
    ipc_subclass = generate_serializer(serializers.CharField, max_length=32)
    ipc_main_group = generate_serializer(serializers.CharField, max_length=32)
    ipc_subgroup = generate_serializer(serializers.CharField, max_length=32)
    ipc_symbol_position = generate_serializer(serializers.CharField, max_length=32)
    ipc_classification_data_source = generate_serializer(
        serializers.CharField, max_length=32
    )
    ipc_classification_value = generate_serializer(serializers.CharField, max_length=32)
    ipc_class_level = generate_serializer(serializers.CharField, max_length=128)
    ipc_class_status = generate_serializer(serializers.CharField, max_length=128)


class GrantedPregrantCrosswalkNestedSerializer(PVAPIDocumentSerializer):
    patent_id = generate_serializer(serializers.CharField, max_length=32)
    document_number = generate_serializer(serializers.CharField, max_length=32)
    application_number = generate_serializer(serializers.CharField, max_length=32)
    current_document_number_flag = generate_serializer(serializers.BooleanField)
    current_patent_id_flag = generate_serializer(serializers.BooleanField)


class GovInterestOrganizationsNestedSerializer(PVAPIDocumentSerializer):
    fedagency_name = generate_serializer(serializers.CharField, max_length=128)
    level_one = generate_serializer(serializers.CharField, max_length=128)
    level_two = generate_serializer(serializers.CharField, max_length=128)
    level_three = generate_serializer(serializers.CharField, max_length=128)


class ForeignPriorityNestedSerializer(PVAPIDocumentSerializer):
    priority_claim_kind = generate_serializer(serializers.CharField, max_length=32)
    foreign_application_id = generate_serializer(serializers.CharField, max_length=32)
    filing_date = generate_serializer(serializers.DateField)
    foreign_country_filed = generate_serializer(serializers.CharField, max_length=32)
    priority_claim_sequence = generate_serializer(serializers.IntegerField)


class PublicationSerializer(PVAPIDocumentSerializer):
    document_number = generate_serializer(serializers.IntegerField)
    publication_title = generate_serializer(serializers.CharField, max_length=2048)
    publication_type = generate_serializer(serializers.CharField, max_length=512)
    publication_country = generate_serializer(serializers.CharField, max_length=32)
    publication_date = generate_serializer(serializers.DateField)
    publication_year = NumericField(required=False)
    publication_abstract = generate_serializer(serializers.CharField, max_length=16384)
    publication_kind = generate_serializer(serializers.CharField, max_length=512)
    publication_application_number = generate_serializer(serializers.IntegerField)
    series_code = generate_serializer(serializers.IntegerField)
    rule_47_flag = generate_serializer(serializers.BooleanField)
    assignees = generate_serializer(
        serializers.ListField, child=AssigneeNestedSerializer()
    )
    cpc_at_issue = generate_serializer(
        serializers.ListField, child=CPCNestedSerializer()
    )
    cpc_current = generate_serializer(
        serializers.ListField, child=CPCNestedSerializer()
    )
    foreign_priority = generate_serializer(
        serializers.ListField, child=ForeignPriorityNestedSerializer(), required=False
    )

    gov_interest_organizations = generate_serializer(
        serializers.ListField, child=GovInterestOrganizationsNestedSerializer()
    )
    granted_pregrant_crosswalk = generate_serializer(
        serializers.ListField, child=GrantedPregrantCrosswalkNestedSerializer()
    )
    inventors = generate_serializer(
        serializers.ListField, child=InventorNestedSerializer()
    )
    ipcr = generate_serializer(serializers.ListField, child=IPCRNestedSerializer())
    pct_data = generate_serializer(
        serializers.ListField, child=PCTDataNestedSerializer()
    )
    us_related_documents = generate_serializer(
        serializers.ListField, child=USRelatedDocumentNestedSerializer(), required=False
    )
    us_parties = generate_serializer(
        serializers.ListField, child=USPartiesNestedSerializer()
    )
    uspc_at_issue = generate_serializer(
        serializers.ListField, child=USPCNestedSerializer()
    )
    wipo = generate_serializer(serializers.ListField, child=WIPONestedSerializer())


class PublicationResponseDocument(APIResponseDocument):
    def __init__(self, error, count, total_hits, publications):
        super(PublicationResponseDocument, self).__init__(error, count, total_hits)
        self.publications = publications


class PublicationEndpoint:
    def __init__(self, **kwargs):
        self.index = "publications"
        self.nested_paths = {
            "assignees": "assignees",
            "inventors": "inventors",
            "cpc_at_issue": "cpc_at_issue",
            "cpc_current": "cpc_current",
            "foreign_priority": "foreign_priority",
            "gov_interest_organizations": "gov_interest_organizations",
            "granted_pregrant_crosswalk": "granted_pregrant_crosswalk",
            "ipcr": "ipcr",
            "pct_data": "pct_data",
            "us_related_documents": "us_related_documents",
            "us_parties": "us_parties",
            "uspc_at_issue": "uspc_at_issue",
            "wipo": "wipo",
        }
        self.operator_translations = {
            "assignees.assignee_organization": {"_eq": "_text_all"},
            "gov_interest_organizations.fedagency_name": {"_eq": "text_all"},
        }
        self.keyword_field_translations = [
            "us_parties.us_party_name_first",
            "us_parties.us_party_name_last",
            "us_parties.us_party_organization",
            "assignees.assignee_city",
            "assignees.assignee_country",
            "assignees.assignee_individual_name_first",
            "assignees.assignee_individual_name_last",
            "assignees.assignee_organization",
            "assignees.assignee_state",
            "gov_interest_organizations.fedagency_name",
            "gov_interest_organizations.level_one",
            "gov_interest_organizations.level_three",
            "gov_interest_organizations.level_two",
            "inventors.inventor_city",
            "inventors.inventor_country",
            "inventors.inventor_name_first",
            "inventors.inventor_name_last",
            "inventors.inventor_state",
        ]
        self.f = ["document_number", "publication_title", "publication_date"]
        self.s = [{"document_number": "asc"}]
        self.field_list = list(
            PublicationSerializer.__dict__["_declared_fields"].keys()
        )
        self.response_encoder = PublicationResponseDocument
        self.response_remapper = [
            {"assignees": {"assignee_id": "assignee"}},
            {"inventors": {"inventor_id": "inventor"}},
            {"uspc_at_issue": {"uspc_mainclass_id": "uspc_mainclass"}},
            {"uspc_at_issue": {"uspc_subclass_id": "uspc_subclass"}},
            {"cpc_current": {"cpc_class_id": "cpc_class"}},
            {"cpc_current": {"cpc_subclass_id": "cpc_subclass"}},
            {"cpc_current": {"cpc_group_id": "cpc_group"}},
        ]


class PublicationList(PVAPIListView, PublicationEndpoint):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        PublicationEndpoint.__init__(self)


class PublicationDetail(PVAPIDetailView, PublicationEndpoint):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        PublicationEndpoint.__init__(self)
        self.pk_field = "document_number"
