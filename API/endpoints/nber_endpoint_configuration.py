from rest_framework import serializers

from API.documents.APIResponse import APIResponseDocument
from API.PVAPIViews import PVAPIDetailView, PVAPIListView
from API.serializers.APISubdocSerializer import PVAPIDocumentSerializer
from API.serializers.custom_serializers_fields import IDHyperlinker


class NBERCategoryLookupSerializer(PVAPIDocumentSerializer):
    nber_category_id = serializers.CharField(max_length=32, required=False)
    nber_category_title = serializers.CharField(max_length=1024, required=False)
    nber_category_num_patents = serializers.IntegerField(required=False)
    nber_category_num_assignees = serializers.IntegerField(required=False)
    nber_category_num_inventors = serializers.IntegerField(required=False)
    nber_category_first_seen_date = serializers.DateField(required=False)
    nber_category_last_seen_date = serializers.DateField(required=False)
    nber_category_years_active = serializers.IntegerField(required=False)


class NBERSubcategoryLookupSerializer(PVAPIDocumentSerializer):
    nber_category = IDHyperlinker(
        read_only=True, many=False, view_name="nber_category-detail"
    )
    nber_subcategory_id = serializers.CharField(max_length=32, required=False)
    nber_subcategory_title = serializers.CharField(max_length=1024, required=False)
    nber_subcategory_num_patents = serializers.IntegerField(required=False)
    nber_subcategory_num_assignees = serializers.IntegerField(required=False)
    nber_subcategory_num_inventors = serializers.IntegerField(required=False)
    nber_subcategory_first_seen_date = serializers.DateField(required=False)
    nber_subcategory_last_seen_date = serializers.DateField(required=False)
    nber_subcategory_years_active = serializers.IntegerField(required=False)


class NBERCategoryResponseDocument(APIResponseDocument):
    def __init__(self, error, count, total_hits, nber_categories):
        super(NBERCategoryResponseDocument, self).__init__(error, count, total_hits)
        self.nber_categories = nber_categories


class NBERSubcategoryResponseDocument(APIResponseDocument):
    def __init__(self, error, count, total_hits, nber_subcategories):
        super().__init__(error, count, total_hits)
        self.nber_subcategories = nber_subcategories


class NBERCategoryEndpoint:
    def __init__(self):
        self.index = "nber_categories"
        self.f = ["nber_category_id", "nber_category_title"]
        self.s = [{"nber_category_id": "asc"}]
        self.field_list = ["nber_category_id", "nber_category_title"]
        self.response_encoder = NBERCategoryResponseDocument
        self.q = {}
        self.lookup = True


class NBERSubCategoryEndpoint:
    def __init__(self):
        self.index = "nber_subcategories"
        self.f = ["nber_subcategory_id", "nber_subcategory_title", "nber_category_id"]
        self.s = [{"nber_subcategory_id": "asc"}]
        self.field_list = [
            "nber_category_id",
            "nber_subcategory_id",
            "nber_subcategory_title",
            "nber_subcategory_num_assignees",
            "nber_subcategory_num_patents",
            "nber_subcategory_num_inventors",
            "nber_subcategory_first_seen_date",
            "nber_subcategory_last_seen_date",
            "nber_subcategory_years_active",
        ]
        self.response_encoder = NBERSubcategoryResponseDocument
        self.response_remapper = [{"nber_category_id": "nber_category"}]
        self.q = {}
        self.lookup = True


class NBERSubCategoryList(PVAPIListView, NBERSubCategoryEndpoint):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        NBERSubCategoryEndpoint.__init__(self)


class NBERSubCategoryDetail(PVAPIDetailView, NBERSubCategoryEndpoint):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        NBERSubCategoryEndpoint.__init__(self)
        self.pk_field = "nber_subcategory_id"


class NBERCategoryList(PVAPIListView, NBERCategoryEndpoint):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        NBERCategoryEndpoint.__init__(self)


class NBERCategoryDetail(PVAPIDetailView, NBERCategoryEndpoint):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        NBERCategoryEndpoint.__init__(self)
        self.pk_field = "nber_category_id"
