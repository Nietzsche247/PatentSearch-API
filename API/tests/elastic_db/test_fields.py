import json

import pytest
from django.conf import settings


def get_test_case_for_group(group, schemas, id_additions, prefix=None):
    for field, config in group["items"]["properties"].items():
        print(field)
        # if field in id_additions:
        # field = f"{field}_id"
        if prefix is not None:
            field = "{prefix}.{field}".format(prefix=prefix, field=field)
        if config["type"] == "array":
            # nested_ref = config['items']["$ref"].split("/")[-1]
            yield from get_test_case_for_group(
                config, schemas, id_additions, prefix=field
            )
        else:
            yield field


def pytest_generate_tests(metafunc):
    """
    Generate test cases from API spec
    :param metafunc:
    """

    if metafunc.function.__name__ == "test_field_count":
        id_additions = ["cpc_group", "section", "subclass"]
        api_spec = json.load(open("API/static/openapi.json", "r"))
        ids = []
        argnames = ["index", "field"]
        argvalues = []
        for index, endpoint in [
            ("patents", "PatentSuccessResponse"),
            ("assignees", "AssigneeSuccessResponse"),
            ("inventors", "InventorSuccessResponse"),
            ("attorneys", "AttorneySuccessResponse"),
            ("cpc_classes", "CpcClassSuccessResponse"),
            ("cpc_subclasses", "CpcSubclassSuccessResponse"),
            ("cpc_groups", "CpcGroupSuccessResponse"),
            ("foreign_citations", "ForeignCitationSuccessResponse"),
            ("inventors", "InventorSuccessResponse"),
            ("ipcr", "IpcClassificationSuccessResponse"),
            ("locations", "LocationSuccessResponse"),
            ("other_references", "OtherreferenceSuccessResponse"),
            ("rel_app_texts", "RelAppTextSuccessResponse"),
            ("us_application_citations", "ApplicationCitationSuccessResponse"),
            ("us_patent_citations", "PatentCitationsSuccessResponse"),
            ("uspc_mainclasses", "UspcMainclassSuccessResponse"),
            ("uspc_subclasses", "UspcSubclassSuccessResponse"),
            ("wipo", "WipoFieldSuccessResponse"),
            ("publications", "PublicationSuccessResponse"),
            ("rel_app_texts", "RelAppTextPubSuccessResponse"),
        ]:
            response_config = api_spec["components"]["schemas"][endpoint]["properties"][
                index
            ]
            for field in get_test_case_for_group(
                response_config, api_spec["components"]["schemas"], id_additions
            ):
                argvalues.append((index, field))
                ids.append(f"{endpoint}->{field}")
        metafunc.parametrize(argnames, argvalues, ids=ids)


@pytest.mark.django_db
def test_field_count(default_search_wrapper, index, field):
    staging_exclusions = [
        ("patents", "pct_data.pct_102_date"),
        ("publications", "pct_data.pct_102_date"),
        ("patents", "us_term_of_grant.disclaimer_date"),
        ("rel_app_texts", "document_number"),
    ]
    if any(
        [
            t in index
            for t in ["brf_sum_text", "claim", "detail_desc_text", "draw_desc_text"]
        ]
    ):
        pytest.skip("Text data not implemented yet")
    if field in ["assignees.assignee", "inventors.inventor"] and index in [
        "patents",
        "publications",
    ]:
        # these nested hyperlinked fields are derived from the corresponding ID and aren't raw in the elastic data
        field = f"{field}_id"
    if "." in field:
        es_query = {
            "query": {
                "nested": {
                    "path": field.split(".")[0],
                    "query": {"bool": {"must": {"exists": {"field": field}}}},
                }
            }
        }
    else:
        es_query = {"query": {"exists": {"field": field}}}

    count_results = default_search_wrapper.es.count(
        index=index, query=es_query["query"]
    )
    if (settings.ENV in ["STAGE", "DEV"]) and ((index, field) in staging_exclusions):
        return
    try:
        assert count_results["count"] > 0
    except AssertionError:
        print(es_query)
        raise  #
