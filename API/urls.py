from django.urls import path, re_path

from API.endpoints.api_key_creation_endpoint import CreateApiKey
from API.endpoints.assignee_endpoint_configuration import AssigneeDetail, AssigneeList
from API.endpoints.attorney_endpoint_configuration import AttorneyDetail, AttorneyList
from API.endpoints.citations_endpoint_configuration import (
    USApplicationCitationDetail,
    USApplicationCitationList,
    USPatentCitationDetail,
    USPatentCitationList,
)
from API.endpoints.cpc_endpoint_configuration import (
    CPCClassDetail,
    CPCClassList,
    CPCGroupDetail,
    CPCGroupList,
    CPCSubClassDetail,
    CPCSubClassList,
)
from API.endpoints.foriegncitation_endpoint_configuration import ForeignCitationList
from API.endpoints.g_brf_sum_text_endpoint_configuration import GBrfSumTextList
from API.endpoints.g_claim_endpoint_configuration import GClaimList
from API.endpoints.g_detail_desc_text_endpoint_configuration import GDetailDescTextList
from API.endpoints.g_draw_desc_text_endpoint_configuration import GDrawDescTextList
from API.endpoints.inventor_endpoint_configuration import InventorDetail, InventorList
from API.endpoints.ipcr_endpoint_configuration import IPCRList
from API.endpoints.location_endpoint_configuration import LocationDetail, LocationList
from API.endpoints.nber_endpoint_configuration import (
    NBERCategoryDetail,
    NBERCategoryList,
    NBERSubCategoryDetail,
    NBERSubCategoryList,
)
from API.endpoints.otherref_endpoint_configuration import OtherReferenceList
from API.endpoints.patent_endpoint_configuration import PatentDetail, PatentList
from API.endpoints.pg_brf_sum_text_endpoint_configuration import PGBrfSumTextList
from API.endpoints.pg_claim_endpoint_configuration import PGClaimList
from API.endpoints.pg_detail_desc_text_endpoint_configuration import (
    PGDetailDescTextList,
)
from API.endpoints.pg_draw_desc_text_endpoint_configuration import PGDrawDescTextList
from API.endpoints.publication_endpoint_configuration import (
    PublicationDetail,
    PublicationList,
)
from API.endpoints.relatedapptext_endpoint_configuration import (
    RelatedTextList,
    RelatedTextPubList,
)
from API.endpoints.uspc_endpoint_configuration import (
    USPCMainclassDetail,
    USPCMainclassList,
    USPCSubclassDetail,
    USPCSubclassList,
)
from API.endpoints.wipo_endpoint_configuration import WIPODetail, WIPOList

# from
urlpatterns = [
    re_path("location/?$", LocationList.as_view(), name="location-list"),
    path("location/<str:pk>/", LocationDetail.as_view(), name="location-detail"),
    re_path("inventor/?$", InventorList.as_view(), name="inventor-list"),
    path("inventor/<str:pk>/", InventorDetail.as_view(), name="inventor-detail"),
    re_path("assignee/?$", AssigneeList.as_view()),
    path("assignee/<str:pk>/", AssigneeDetail.as_view(), name="assignee-detail"),
    re_path("patent/us_patent_citation/?$", USPatentCitationList.as_view()),
    path("patent/us_patent_citation/<str:pk>/", USPatentCitationDetail.as_view()),
    re_path("patent/us_application_citation/?$", USApplicationCitationList.as_view()),
    path(
        "patent/us_application_citation/<str:pk>/",
        USApplicationCitationDetail.as_view(),
    ),
    path("cpc_class/<str:pk>/", CPCClassDetail.as_view(), name="cpc_class-detail"),
    re_path("cpc_class/?$", CPCClassList.as_view(), name="cpc_class-list"),
    path(
        "cpc_subclass/<str:pk>/",
        CPCSubClassDetail.as_view(),
        name="cpc_subclass-detail",
    ),
    re_path("cpc_subclass/?$", CPCSubClassList.as_view()),
    path("cpc_group/<str:pk>/", CPCGroupDetail.as_view(), name="cpc_group-detail"),
    re_path("cpc_group/?$", CPCGroupList.as_view(), name="cpc_group-list"),
    path(
        "uspc_mainclass/<str:pk>/",
        USPCMainclassDetail.as_view(),
        name="uspc_mainclass-detail",
    ),
    re_path("uspc_mainclass/?$", USPCMainclassList.as_view()),
    path(
        "uspc_subclass/<str:pk>/",
        USPCSubclassDetail.as_view(),
        name="uspc_subclass-detail",
    ),
    re_path("uspc_subclass/?$", USPCSubclassList.as_view()),
    path(
        "nber_category/<str:pk>/",
        NBERCategoryDetail.as_view(),
        name="nber_category-detail",
    ),
    re_path("nber_category/?$", NBERCategoryList.as_view()),
    path(
        "nber_subcategory/<str:pk>/",
        NBERSubCategoryDetail.as_view(),
        name="nber_subcategory-detail",
    ),
    re_path("nber_subcategory/?$", NBERSubCategoryList.as_view()),
    re_path("ipc/?$", IPCRList.as_view(), name="ipc-list"),
    re_path("wipo/?$", WIPOList.as_view()),
    path("wipo/<str:pk>/", WIPODetail.as_view(), name="wipo-detail"),
    re_path("patent/attorney/?$", AttorneyList.as_view()),
    path("patent/attorney/<str:pk>/", AttorneyDetail.as_view(), name="attorney-detail"),
    re_path("patent/foreign_citation/?$", ForeignCitationList.as_view()),
    re_path("patent/other_reference/?$", OtherReferenceList.as_view()),
    re_path("patent/rel_app_text/?$", RelatedTextList.as_view()),
    path("patent/<str:pk>/", PatentDetail.as_view(), name="patent-detail"),
    re_path("patent/?$", PatentList.as_view(), name="patent-list"),
    re_path(
        "publication/rel_app_text/?$",
        RelatedTextPubList.as_view(),
        name="rel-app-text-pub-list",
    ),
    path(
        "publication/<str:pk>/", PublicationDetail.as_view(), name="publication-detail"
    ),
    re_path("publication/?$", PublicationList.as_view(), name="publication-list"),
    path("create-key/", CreateApiKey.as_view()),
    re_path("g_brf_sum_text/?$", GBrfSumTextList.as_view(), name="g_brf_sum_text-list"),
    re_path("g_claim/?$", GClaimList.as_view(), name="g_claim-list"),
    re_path(
        "g_detail_desc_text/?$",
        GDetailDescTextList.as_view(),
        name="g_detail_desc_text-list",
    ),
    re_path(
        "g_draw_desc_text/?$", GDrawDescTextList.as_view(), name="g_draw_desc_text-list"
    ),
    re_path(
        "pg_brf_sum_text/?$", PGBrfSumTextList.as_view(), name="pg_brf_sum_text-list"
    ),
    re_path("pg_claim/?$", PGClaimList.as_view(), name="pg_claim-list"),
    re_path(
        "pg_detail_desc_text/?$",
        PGDetailDescTextList.as_view(),
        name="pg_detail_desc_text-list",
    ),
    re_path(
        "pg_draw_desc_text/?$",
        PGDrawDescTextList.as_view(),
        name="pg_draw_desc_text-list",
    ),
]

# urlpatterns = format_suffix_patterns(urlpatterns)
