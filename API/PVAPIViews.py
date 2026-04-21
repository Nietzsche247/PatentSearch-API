import json
from json import JSONDecodeError
from typing import Any, Dict, List

from django.http import Http404
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from API.documents.APIResponse import APIDataContainer
from API.exceptions import (
    InvalidFieldException,
    InvalidJSONFormatError,
    InvalidOptionException,
    InvalidPagingException,
    InvalidQueryStringError,
)
from API.models import APIUserKey
from API.permissions import HasAPIKeyCreationPermission, HasValidAPIKey
from API.queryparser import QueryParser, extract_key_value
from API.search import PatentsViewElasticSearch
from API.UsageLogging import log_usage


class PVAPIView(APIView):
    response_remapper: List[Dict[str, Any]]  # here as a requirement for inheritance

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        # Query parameter, Field list, options list and sort options init
        self.q = None
        self.f = None
        self.o = {}
        self.s = None
        # Init structure to store ES query
        self.parsed_query = {"query": None}
        self.parsed_sort = None
        # ES Index
        self.index = None
        # Init structure to store ES nested document paths
        self.nested_paths = {}
        # Init allowed list of fields
        self.field_list = None
        # Encode data returned by ES into a Python object
        self.data_encoder = APIDataContainer
        # Encoder to translate python object to serializer format
        self.response_encoder = None
        # Default API endpoint type (lookup v not lookup)
        self.lookup = False
        # Init Remapping ES fields into API fields
        self.response_remapper = []
        # Translate user entered query into a different structure/operations
        self.operator_translations = {}
        # Suffix keyword for "text" fields
        self.keyword_field_translations = []
        # List of involved fields
        self.involved_fields = {}
        # Query Parser
        self.parser = QueryParser()

    def parse_get_parameter_string(
        self, query_string: str, field_string: str, sort_string: str, option_string: str
    ) -> None:
        """Parses various api request parameters stores python representation in the PVAPIView object

        :param query_string: string containing the 'q' parameter of PatentsView PatentSearch API
        :param field_string: string containing the 'f' parameter of PatentsView PatentSearch API
        :param sort_string: string containing the 's' parameter of PatentsView PatentSearch API
        :param option_string: string containing the 'o' parameter of PatentsView PatentSearch API
        """
        try:
            self.q = json.loads(query_string)
        except JSONDecodeError:
            raise InvalidJSONFormatError("Invalid JSON in 'q' parameter")
        except TypeError:
            # Lookup endpoints are allowed to query without 'q' parameter
            if not self.lookup:
                raise InvalidQueryStringError("Query String is missing")
        if field_string:
            try:
                self.f = json.loads(field_string)
            except JSONDecodeError:
                raise InvalidJSONFormatError("Invalid JSON in 'f' parameter")
        if sort_string:
            try:
                self.s = json.loads(sort_string)
            except JSONDecodeError:
                raise InvalidJSONFormatError("Invalid JSON in 's' parameter")
        if option_string:
            try:
                self.o = json.loads(option_string)
                # If the request involves sorting by 1 field AND the 'after' is a value then wrap the element in list
            except JSONDecodeError:
                raise InvalidJSONFormatError("Invalid JSON in 'o' parameter")

    def validate_request_parameters(self) -> None:
        """Validates the field list, query parameter, sort and options strings. Common to all endpoints and methods"""
        # Q
        if len(self.q) < 1 and not self.lookup:
            raise InvalidQueryStringError("Query String is missing")
        if len(self.q) > 1:
            raise InvalidQueryStringError(
                "Query string should have only one 'key-value' pair"
            )

        # F
        for i, f in enumerate(self.f):
            if f not in self.field_list and not any(
                [f.startswith(f"{x}.") for x in self.field_list]
            ):
                raise InvalidFieldException(f"Invalid field: {f}")
            if "." in f:
                nested_document = f.split(".", 1)[0]
                if nested_document not in self.nested_paths:
                    raise InvalidFieldException(
                        f"Invalid field: {f}. {nested_document} is not a nested field"
                    )
            self.involved_fields[f] = 1

        # S
        for s in self.s:
            try:
                field, direction = extract_key_value(s)
            except AttributeError:
                raise InvalidQueryStringError("'s' parameter invalid")
            if field not in self.field_list:
                raise InvalidFieldException("Invalid field: {f}".format(f=field))
            self.involved_fields[field] = 1

        # O
        if "after" in self.o:
            if len(self.o["after"]) != len(self.s):
                raise InvalidPagingException(
                    "Sort option had {x} elements but 'after' had {y}".format(
                        x=len(self.s), y=len(self.o["after"])
                    )
                )

        if "offset" in self.o:
            raise InvalidPagingException(
                "'offset' has been replaces with 'after' parameter."
            )

        if "pad_patent_id" in self.o:
            # attempt to interpret string and integer variations of true/false
            if isinstance(self.o["pad_patent_id"], str):
                self.o["pad_patent_id"] = self.o["pad_patent_id"].strip().lower()
            if self.o["pad_patent_id"] in (0, "false"):
                self.o["pad_patent_id"] = False
            elif self.o["pad_patent_id"] in (1, "true"):
                self.o["pad_patent_id"] = True
            if not isinstance(self.o["pad_patent_id"], bool):
                raise InvalidOptionException(
                    f"""'pad_patent_id' option provided with non-boolean value: {self.o["pad_patent_id"]}"""
                )

        if self.o.get("pad_patent_id", False) and ("patent_id" in self.f):
            self.f.remove("patent_id")
            self.f.append("patent_zero_prefix")

        if self.__class__.__name__ == "PatentList":
            # this option unused by other endpoint classes
            if "exclude_withdrawn" in self.o:
                # attempt to interpret string and integer variations of true/false
                if isinstance(self.o["exclude_withdrawn"], str):
                    self.o["exclude_withdrawn"] = (
                        self.o["exclude_withdrawn"].strip().lower()
                    )
                if self.o["exclude_withdrawn"] in (0, "false"):
                    self.o["exclude_withdrawn"] = False
                elif self.o["exclude_withdrawn"] in (1, "true"):
                    self.o["exclude_withdrawn"] = True
                if not isinstance(self.o["exclude_withdrawn"], bool):
                    raise InvalidOptionException(
                        f"""'exclude_withdrawn' option provided with non-boolean value: {self.o["exclude_withdrawn"]}"""
                    )

    def process_request_params(self):
        if "after" in self.o and not isinstance(self.o["after"], list):
            self.o["after"] = [self.o["after"]]

    def parse_request_to_api(self):
        # determine whether patent queries need to exclude withdrawn patents (default yes)
        if (
            self.__class__.__name__
            == "PatentList"  # only matters for the Patent endpoint
            and self.o.get(
                "exclude_withdrawn", True
            )  # default to true if user does not specify
            and '{"withdrawn": true}'
            not in json.dumps(
                self.q
            )  # override default if query specifically requests withdrawn patents
        ):
            # if so, just wrap the given query in an "_and" with {"withdrawn":False}
            self.q = {"_and": [{"withdrawn": False}, self.q]}
        # Parse query input
        try:
            self.parsed_query["query"] = self.parser.generate_search_query(
                self.q,
                self.nested_paths,
                self.field_list,
                self.keyword_field_translations,
                self.o.get("pad_patent_id", False),
            )
            self.involved_fields.update(self.parser.get_involved_fields())
        except IndexError:
            if not self.lookup:
                raise
            else:
                self.parsed_query = {}

        # Then parse sort input
        if len(self.s) > 0:
            self.parsed_sort = []
            for sort_spec in self.s:
                field, value = extract_key_value(sort_spec)
                if field == "patent_id" and self.o.get("pad_patent_id", False):
                    # ensure sorting correctly handles inconsistent zero-padding in non-utility patents
                    field = "patent_zero_prefix"
                if field in self.keyword_field_translations:
                    field = f"{field}.keyword"
                self.parsed_sort.append({field: value})

    def query_handler(self, request):
        from API.serializers.APISerializer import APISerializer

        self.process_request_params()
        self.validate_request_parameters()
        self.parse_request_to_api()
        api_response = self.process_query()
        serializer = APISerializer(api_response, context={"request": request})
        response_object = Response(serializer.data)
        return response_object

    def generate_response_field_list(self):
        response_field_list = []
        # Generate a list of fields used in query. Rename fields based on response remapper. (Only for non-nested field)
        for item in self.field_list:
            fname = item
            # Structure of response remapper shadows the structure of data returned Elastic Database collection.
            # Each element of response remapper is a dictionary of mapping
            for mapping_item in self.response_remapper:
                # mapping item is dictionary.
                # The key is the "group"/"entity" ("assignees", "inventors", etc.)
                # The value is a dictionary of Elastic Database field name and their ES API counterpart
                if item in mapping_item and not isinstance(mapping_item[item], dict):
                    # If the field specified is the current "group"/"entity"
                    # and if the value itself is not a dict (i.e. the mapping is for  non-nested field)
                    # fname = mapping_item[item]
                    response_field_list.append(mapping_item[item])
            response_field_list.append(fname)
        return response_field_list

    def get_elastic_response(self):
        size = self.o.get("size", 100)
        offset = self.o.get("after", None)
        if size > 1000:
            size = 1000
        # searcher = PatentsViewElasticSearch.from_config()
        searcher = PatentsViewElasticSearch.from_django_settings()
        api_response = searcher.search(
            index=self.index,
            query=self.parsed_query,
            fields=self.f,
            size=size,
            offset=offset,
            sort=self.parsed_sort,
        )
        count_response = searcher.count(index=self.index, query=self.parsed_query)
        return api_response, count_response

    def convert_elastic_response_to_api_response(
        self, api_response, response_field_list
    ):
        result_records = []
        es_records = api_response["hits"]["hits"]
        # Rename _id fields without _id. Used for IDHyperlinker
        for record in es_records:
            for mapping_item in self.response_remapper:
                record["_source"] = map_record(mapping_item, record["_source"])
            encoding_data = {}
            for key, value in record["_source"].items():
                # If field name returned from the Elastic database
                # (1) is one of the request field names
                # (2) OR contains the requested field name after removing wild card (indicating nested fields)
                # (3) OR is one of the renamed fields according to response remapper (renamed in above section)
                if (
                    key in self.f
                    or any([x.replace("*", "").startswith(key) for x in self.f])
                    or key in [extract_key_value(x)[0] for x in self.response_remapper]
                ):
                    if key == "patent_zero_prefix":
                        # patent_zero_prefix should only appear when inserted by "pad_patent_id"
                        # in these cases it should be returned as "patent_id"
                        key = "patent_id"
                    encoding_data[key] = value
            # Data Encoder is generic python class that allows its object to have any field name and values
            # See APIDataContainer
            encoded_data = self.data_encoder(
                fields=response_field_list, **encoding_data
            )
            result_records.append(encoded_data)
        return result_records

    def process_query(self):
        # Get query results from elastic search database
        api_response, count_response = self.get_elastic_response()
        # Get list of fields that are to be returned in the API response
        response_field_list = self.generate_response_field_list()
        # Convert data structure returned from Elasticsearch database (plain ol' dictionary)
        # to API's structure ( a generic and flexible Python class object)
        result_records = self.convert_elastic_response_to_api_response(
            api_response, response_field_list
        )
        api_response_dict = {
            "count": len(result_records),
            "total_hits": count_response["count"],
            self.index: result_records,
            "error": False,
        }
        api_response_record = self.response_encoder(**api_response_dict)
        return api_response_record


def map_record(mapping_item: dict, record_source: dict):
    """
    Recursively rename fields in a dictionary according to mapping item provided
    :param mapping_item: A dict providing the renaming rules ("from" and "to")
    :param record_source: The dictionary to be renamed
    :return: renamed dictionary
    """
    key, value = extract_key_value(mapping_item)
    # the value being a dictionary indicates a nested renaming
    if isinstance(value, dict):
        if key in record_source:
            record_source[key] = map_record(value, record_source[key])
    else:
        # If the record source is a list then apply renaming to each item in the list
        # Typically happens at depth>0
        if isinstance(record_source, list):
            for rs in record_source:
                if key in rs:
                    data = rs.get(key)
                    if isinstance(data, str):
                        # Renaming/mapping happens typically on "key" fields (patent id, uspc_mainclass_id etc.
                        # Ensure '/' are replaced by ':' to support IDHyperlinker
                        data = data.replace("/", ":")
                    rs[value] = data
                    # rs[key]=data
        else:
            if key in record_source:
                data = record_source.get(key)
                if isinstance(data, str):
                    data = data.replace("/", ":")
                record_source[value] = data
                # record_source[key] = data
    return record_source


class PVAPIDetailView(PVAPIView):
    def __init__(self, **kwargs):
        from API.serializers.APISerializer import APISerializer

        self.serializer_class = APISerializer

        super().__init__(**kwargs)
        self.pk_field = None

    def get(self, request, pk, format=None):
        q = json.dumps({self.pk_field: pk})
        self.parse_get_parameter_string(
            q,
            option_string=request.query_params.get("o"),
            sort_string=request.query_params.get("s"),
            field_string=request.query_params.get("f"),
        )

        get_response = self.query_handler(request)
        if get_response.data["total_hits"] == 0:
            log_usage(request, self.index, 404, exception=0, fields_involved=[])
            raise Http404
        log_usage(
            request, self.index, 200, exception=0, fields_involved=self.involved_fields
        )
        return get_response


class PVAPIListView(PVAPIView):
    def __init__(self, **kwargs):
        from API.serializers.APISerializer import APISerializer

        self.serializer_class = APISerializer

        super().__init__(**kwargs)

    def get(self, request: Request, format=None):
        self.parse_get_parameter_string(
            request.query_params.get("q"),
            option_string=request.query_params.get("o"),
            sort_string=request.query_params.get("s"),
            field_string=request.query_params.get("f"),
        )
        log_usage(
            request, self.index, 200, exception=0, fields_involved=self.involved_fields
        )
        return self.query_handler(request)

    def post(self, request: Request, format=None):
        try:
            self.q = request.data["q"]
        except KeyError:
            if not self.lookup:
                raise InvalidQueryStringError("Query String is missing")
        except TypeError:
            raise InvalidJSONFormatError(
                "POST method expects JSON objects in the body. Found string representation of JSON instead "
            )

        try:
            self.f = request.data["f"]
        except KeyError:
            pass

        try:
            self.s = request.data["s"]
        except KeyError:
            pass

        try:
            self.o = request.data["o"]
        except KeyError:
            pass

        self.process_request_params()
        # self.process_request_params(
        #     option_string=request.query_params.get('o'),
        #     sort_string=request.query_params.get('s'),
        #     field_string=request.query_params.get('f'))
        response = self.query_handler(request)
        log_usage(
            request, self.index, 200, exception=0, fields_involved=self.involved_fields
        )
        return response


class APIKeyView(APIView):
    permission_classes = [HasAPIKeyCreationPermission & HasValidAPIKey]

    def post(self, request):
        name = request.data.get("name")
        username = request.data.get("username")
        email = request.data.get("email")

        revoked_keys = self._revoke_keys(email=email)

        _, key = APIUserKey.objects.create_key(
            name=name, username=username, email=email
        )

        response = {
            "name": name,
            "username": username,
            "email": email,
            "api_key": key,
            "can_create_key": False,
            "revoked_keys": revoked_keys,
        }

        return Response(response)

    def _revoke_keys(self, **kwargs):
        keys = APIUserKey.objects.filter(revoked=0, **kwargs)

        for key in keys:
            key.revoke()

        return [key.name for key in keys]
