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
    patent_number = generate_serializer(serializers.CharField, max_length=48)


class ApplicationNestedSerializer(PVAPIDocumentSerializer):
    application_id = generate_serializer(serializers.CharField, max_length=16)
    application_type = generate_serializer(serializers.CharField, max_length=8)
    filing_date = generate_serializer(serializers.DateField)
    series_code = generate_serializer(serializers.CharField, max_length=8)
    rule_47_flag = generate_serializer(serializers.BooleanField)
    filing_type = generate_serializer(serializers.CharField, max_length=8)


class ApplicantNestedSerializer(PVAPIDocumentSerializer):
    applicant_name_first = generate_serializer(serializers.CharField, max_length=64)
    applicant_name_last = generate_serializer(serializers.CharField, max_length=64)
    applicant_organization = generate_serializer(serializers.CharField, max_length=256)
    applicant_sequence = generate_serializer(serializers.IntegerField)
    applicant_designation = generate_serializer(serializers.CharField, max_length=8)
    applicant_type = generate_serializer(serializers.CharField, max_length=32)
    location_id = generate_serializer(serializers.CharField, max_length=128)


class AttorneyNestedSerializer(PVAPIDocumentSerializer):
    attorney_id = generate_serializer(serializers.CharField, max_length=128)
    attorney_sequence = generate_serializer(serializers.IntegerField)
    attorney_name_first = generate_serializer(serializers.CharField, max_length=32)
    attorney_name_last = generate_serializer(serializers.CharField, max_length=32)
    attorney_organization = generate_serializer(serializers.CharField, max_length=128)


class ExaminerNestedSerializer(PVAPIDocumentSerializer):
    examiner_id = generate_serializer(serializers.CharField, max_length=128)
    examiner_first_name = generate_serializer(serializers.CharField, max_length=32)
    examiner_last_name = generate_serializer(serializers.CharField, max_length=32)
    examiner_role = generate_serializer(serializers.CharField, max_length=32)
    art_group = generate_serializer(serializers.CharField, max_length=32)


class WIPONestedSerializer(PVAPIDocumentSerializer):
    wipo_field = IDHyperlinker(read_only=True, many=False, view_name="wipo-detail")
    wipo_field_id = generate_serializer(serializers.CharField, max_length=32)
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


class USTermGrantNestedSerializer(PVAPIDocumentSerializer):
    term_grant = generate_serializer(serializers.CharField, max_length=32)
    term_extension = generate_serializer(serializers.CharField, max_length=32)
    term_disclaimer = generate_serializer(serializers.CharField, max_length=64)
    disclaimer_date = generate_serializer(serializers.DateField)


class USRelatedDocumentNestedSerializer(PVAPIDocumentSerializer):
    related_doc_type = generate_serializer(serializers.CharField, max_length=32)
    related_doc_kind = generate_serializer(serializers.CharField, max_length=32)
    related_doc_number = generate_serializer(serializers.CharField, max_length=32)
    published_country = generate_serializer(serializers.CharField, max_length=32)
    related_doc_published_date = generate_serializer(serializers.DateField)
    related_doc_status = generate_serializer(serializers.CharField, max_length=32)
    related_doc_sequence = generate_serializer(serializers.IntegerField)
    wipo_kind = generate_serializer(serializers.CharField, max_length=32)


class PCTDataNestedSerializer(PVAPIDocumentSerializer):
    published_filed_date = generate_serializer(serializers.DateField)
    pct_102_date = generate_serializer(serializers.DateField)
    pct_371_date = generate_serializer(serializers.DateField)
    application_kind = generate_serializer(serializers.CharField, max_length=32)
    pct_doc_number = generate_serializer(serializers.CharField, max_length=32)
    pct_doc_type = generate_serializer(serializers.CharField, max_length=32)


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


class GrantedPregrantCrosswalkNestedSerializer(PVAPIDocumentSerializer):
    patent_id = generate_serializer(serializers.CharField, max_length=32)
    document_number = generate_serializer(serializers.CharField, max_length=32)
    pgpubs_document_number = generate_serializer(serializers.CharField, max_length=32)
    application_number = generate_serializer(serializers.CharField, max_length=32)


class GovInterestOrganizationsNestedSerializer(PVAPIDocumentSerializer):
    fedagency_name = generate_serializer(serializers.CharField, max_length=128)
    level_one = generate_serializer(serializers.CharField, max_length=128)
    level_two = generate_serializer(serializers.CharField, max_length=128)
    level_three = generate_serializer(serializers.CharField, max_length=128)


class GovInterestContractAwardNumberNestedSerializer(PVAPIDocumentSerializer):
    award_number = generate_serializer(serializers.CharField, max_length=32)


class ForeignPriorityNestedSerializer(PVAPIDocumentSerializer):
    priority_claim_sequence = generate_serializer(serializers.IntegerField)
    priority_claim_kind = generate_serializer(serializers.CharField, max_length=32)
    foreign_application_id = generate_serializer(serializers.CharField, max_length=32)
    filing_date = generate_serializer(serializers.DateField)
    foreign_country_filed = generate_serializer(serializers.CharField, max_length=32)


class BotanicNestedSerializer(PVAPIDocumentSerializer):
    latin_name = generate_serializer(serializers.CharField, max_length=32)
    variety = generate_serializer(serializers.CharField, max_length=32)


class FiguresNestedSerializer(PVAPIDocumentSerializer):
    num_figures = generate_serializer(serializers.IntegerField)
    num_sheets = generate_serializer(serializers.IntegerField)


class PatentSerializer(PVAPIDocumentSerializer):
    # standard fields
    patent_id = generate_serializer(serializers.CharField, max_length=32)
    patent_title = generate_serializer(serializers.CharField, max_length=2048)
    patent_type = generate_serializer(serializers.CharField, max_length=512)
    patent_country = generate_serializer(serializers.CharField, max_length=32)
    patent_date = generate_serializer(serializers.DateField)
    patent_year = NumericField(required=False)
    patent_abstract = generate_serializer(serializers.CharField, max_length=16384)
    patent_cpc_current_group_average_patent_processing_days = NumericField(
        required=False
    )
    withdrawn = generate_serializer(serializers.BooleanField)
    patent_detail_desc_length = NumericField(required=False)
    patent_earliest_application_date = generate_serializer(serializers.DateField)
    patent_num_foreign_documents_cited = NumericField(required=False)
    patent_num_times_cited_by_us_patents = NumericField(required=False)
    patent_num_total_documents_cited = NumericField(required=False)
    patent_num_us_applications_cited = NumericField(required=False)
    patent_num_us_patents_cited = NumericField(required=False)
    patent_processing_days = NumericField(required=False)
    patent_term_extension = NumericField(required=False)
    gov_interest_statement = generate_serializer(
        serializers.CharField, max_length=16384
    )
    patent_uspc_current_mainclass_average_patent_processing_days = NumericField(
        required=False
    )
    wipo_kind = generate_serializer(serializers.CharField, max_length=10)
    # nested fields
    application = generate_serializer(
        serializers.ListField, child=ApplicationNestedSerializer()
    )
    applicants = generate_serializer(
        serializers.ListField, child=ApplicantNestedSerializer()
    )
    assignees = generate_serializer(
        serializers.ListField, child=AssigneeNestedSerializer()
    )
    attorneys = generate_serializer(
        serializers.ListField, child=AttorneyNestedSerializer()
    )
    botanic = generate_serializer(
        serializers.ListField, child=BotanicNestedSerializer()
    )
    cpc_at_issue = generate_serializer(
        serializers.ListField, child=CPCNestedSerializer()
    )
    cpc_current = generate_serializer(
        serializers.ListField, child=CPCNestedSerializer()
    )
    examiners = generate_serializer(
        serializers.ListField, child=ExaminerNestedSerializer()
    )
    foreign_priority = generate_serializer(
        serializers.ListField, child=ForeignPriorityNestedSerializer(), required=False
    )
    figures = generate_serializer(
        serializers.ListField, child=FiguresNestedSerializer()
    )
    gov_interest_contract_award_numbers = generate_serializer(
        serializers.ListField,
        child=GovInterestContractAwardNumberNestedSerializer(),
        required=False,
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
    us_term_of_grant = generate_serializer(
        serializers.ListField, child=USTermGrantNestedSerializer()
    )
    uspc_at_issue = generate_serializer(
        serializers.ListField, child=USPCNestedSerializer()
    )
    wipo = generate_serializer(serializers.ListField, child=WIPONestedSerializer())


class PatentResponseDocument(APIResponseDocument):
    def __init__(self, error, count, total_hits, patents):
        super(PatentResponseDocument, self).__init__(error, count, total_hits)
        self.patents = patents


class PatentEndpoint:
    def __init__(self, **kwargs):
        self.index = "patents"
        self.nested_paths = {
            "assignees": "assignees",
            "inventors": "inventors",
            "application": "application",
            "applicants": "applicants",
            "attorneys": "attorneys",
            "botanic": "botanic",
            "cpc_at_issue": "cpc_at_issue",
            "cpc_current": "cpc_current",
            "examiners": "examiners",
            "foreign_priority": "foreign_priority",
            "figures": "figures",
            "gov_interest_contract_award_numbers": "gov_interest_contract_award_numbers",
            "gov_interest_organizations": "gov_interest_organizations",
            "granted_pregrant_crosswalk": "granted_pregrant_crosswalk",
            "ipcr": "ipcr",
            "pct_data": "pct_data",
            "us_related_documents": "us_related_documents",
            "us_term_of_grant": "us_term_of_grant",
            "uspc_at_issue": "uspc_at_issue",
            "wipo": "wipo",
        }
        self.operator_translations = {
            "assignees.assignee_organization": {"_eq": "_text_all"},
            "gov_interest_organizations.fedagency_name": {"_eq": "text_all"},
        }
        self.keyword_field_translations = [
            "applicants.applicant_name_first",
            "applicants.applicant_name_last",
            "applicants.applicant_organization",
            "assignees.assignee_city",
            "assignees.assignee_country",
            "assignees.assignee_individual_name_first",
            "assignees.assignee_individual_name_last",
            "assignees.assignee_organization",
            "assignees.assignee_state",
            "attorneys.attorney_name_first",
            "attorneys.attorney_name_last",
            "attorneys.attorney_organization",
            "examiners.examiner_first_name",
            "examiners.examiner_last_name",
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
        self.f = ["patent_id", "patent_title", "patent_date"]
        self.o = {"pad_patent_id": False, "exclude_withdrawn": True}
        self.s = [{"patent_id": "asc"}]
        self.field_list = list(PatentSerializer.__dict__["_declared_fields"].keys())
        self.response_encoder = PatentResponseDocument
        self.response_remapper = [
            {"assignees": {"assignee_id": "assignee"}},
            {"inventors": {"inventor_id": "inventor"}},
            {"wipo": {"wipo_field_id": "wipo_field"}},
            {"uspc_at_issue": {"uspc_mainclass_id": "uspc_mainclass"}},
            {"uspc_at_issue": {"uspc_subclass_id": "uspc_subclass"}},
            {"cpc_current": {"cpc_class_id": "cpc_class"}},
            {"cpc_current": {"cpc_subclass_id": "cpc_subclass"}},
            {"cpc_current": {"cpc_group_id": "cpc_group"}},
        ]


class PatentList(PVAPIListView, PatentEndpoint):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        PatentEndpoint.__init__(self)


class PatentDetail(PVAPIDetailView, PatentEndpoint):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        PatentEndpoint.__init__(self)
        self.pk_field = "patent_id"
