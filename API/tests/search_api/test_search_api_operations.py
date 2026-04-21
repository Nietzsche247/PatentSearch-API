import csv
import json
import os
import time

import pytest
from django.urls import resolve
from pymysql import connect as pymysql_connect
from pymysql import cursors


def get_f_names(props, prefix=None):
    for name, schema in props["items"]["properties"].items():
        if schema["type"] == "array":
            yield from get_f_names(schema, prefix=name)
        else:
            pname = name
            if prefix is not None:
                pname = f"{prefix}.{name}"
            yield pname


def process_db_record(record):
    filtered_row = {k: v for k, v in record.items() if v}
    record.clear()
    record.update(filtered_row)
    for key, value in record.items():
        if isinstance(value, str):
            if value.startswith("["):
                try:
                    record[key] = json.loads(record[key])
                except json.decoder.JSONDecodeError:
                    pass
    return record


def get_db_results(database, sql, params=None):
    connection = pymysql_connect(
        host=os.getenv("INGEST_HOST"),
        user=os.getenv("INGEST_USER"),
        password=os.getenv("INGEST_PASSWORD"),
        database=database,
        port=int(os.getenv("INGEST_PORT")),
    )

    with connection.cursor(cursors.DictCursor) as cur:
        cur.execute(sql, params)
        # results = cur.fetchall()
        final_results = []
        special_processing = False
        while True:
            results = cur.fetchmany(10000)
            for row in results:
                if not special_processing and any(
                    v.startswith("[") for v in row.values() if isinstance(v, str)
                ):
                    special_processing = True
                if special_processing:
                    row = process_db_record(row)
                final_results.append(row)
            if len(results) < 10000:
                break
    return final_results


def pytest_generate_tests(metafunc):
    """
    Generate test cases from API spec
    :param metafunc:
    """

    if metafunc.function.__name__ == "test_operator":
        ids = []
        argnames = [
            "endpoint",
            "q_field",
            "f_field",
            "key",
            "entity",
            "database",
            "validation_sql",
        ]
        argvalues = []
        for endpoint, entity, database in [
            ("patent", "patents", os.getenv("INGEST_ES_PATENT")),
            ("publication", "publications", os.getenv("INGEST_ES_PGPUB")),
            (
                "rel-app-text-pub",
                "rel_app_text_publications",
                os.getenv("INGEST_ES_PGPUB"),
            ),
            # TODO: reinstate after updating test artifacts with stable text data
            #  ("g_brf_sum_text", "g_brf_sum_texts", os.getenv("INGEST_ES_PATENT")),
            #  ("g_claim", "g_claims", os.getenv("INGEST_ES_PATENT")),
            #  ("g_draw_desc_text", "g_draw_desc_texts", os.getenv("INGEST_ES_PATENT")),
            #  ("g_detail_desc_text", "g_detail_desc_texts", os.getenv("INGEST_ES_PATENT"))
        ]:
            endpoint_cases_file = f"API/tests/artifacts/{endpoint}_q_test_cases.csv"
            with open(endpoint_cases_file, "r", newline="", encoding="utf-8-sig") as fp:
                reader = csv.DictReader(
                    fp, delimiter=",", quotechar='"', quoting=csv.QUOTE_NONNUMERIC
                )
                for record in reader:
                    # continue
                    argvalues.append(
                        (
                            endpoint,
                            record["q_field"],
                            record["f"],
                            record["key"],
                            entity,
                            database,
                            record["validation_sql"],
                        )
                    )
                    ids.append(f"{endpoint}->{record['case_id']}")
                # argvalues.append(
                #     (endpoint, record['q_field'], record['key'], record['f'], record['validation_file']))
                # ids.append(f"{endpoint}->{record['case_id']}")
        metafunc.parametrize(argnames, argvalues, ids=ids)
    if metafunc.function.__name__ == "test_field":
        ids = []
        openapi = json.load(open("API/static/openapi.json"))
        argnames = ["endpoint", "q_field", "f_field", "key", "entity"]
        argvalues = []
        for endpoint, entity, key in [
            ("Patent", "patents", "patent_id"),
            ("Publication", "publications", "document_number"),
        ]:
            schema_name = f"{endpoint}SuccessResponse"
            for fname in get_f_names(
                openapi["components"]["schemas"][schema_name]["properties"][entity]
            ):
                if entity == "patents":
                    argvalues.append(
                        (
                            endpoint.lower(),
                            json.dumps({"patent_id": "8087582"}),
                            json.dumps([fname]),
                            key,
                            entity,
                        )
                    )
                else:
                    argvalues.append(
                        (
                            endpoint.lower(),
                            json.dumps({"document_number": "20120017343"}),
                            json.dumps([fname]),
                            key,
                            entity,
                        )
                    )
                ids.append(f"{fname}")
            # argvalues.append(
            #     (endpoint, record['q_field'], record['key'], record['f'], record['validation_file']))
            # ids.append(f"{endpoint}->{record['case_id']}")
        metafunc.parametrize(argnames, argvalues, ids=ids)
    if metafunc.function.__name__ == "test_s_field":
        ids = []
        openapi = json.load(open("API/static/openapi.json"))
        argnames = ["endpoint", "q_field", "s_field", "key", "entity"]
        argvalues = []
        for endpoint, entity, id in [
            ("inventor", "inventors", "00ho3lffb5y5gsfp0nfxv9pd7"),
            ("location", "locations", "00006da3-cb90-11eb-9615-121df0c29c1e"),
            ("cpc_class", "cpc_classes", "A01"),
            ("cpc_group", "cpc_groups", "A01B1/00"),
            ("ipc", "ipcr", "1"),
        ]:
            schema_name = f"/api/v1/{endpoint}/"
            key = f"{endpoint}_id"
            q_field = json.dumps({key: id})
            parameter = next(
                (
                    param
                    for param in openapi["paths"][schema_name]["get"]["parameters"]
                    if param["name"] == "s"
                ),
                None,
            )
            s_field = parameter["schema"]["default"]
            argvalues.append((endpoint, q_field, s_field, key, entity))
            ids.append(f"{endpoint}")
        metafunc.parametrize(argnames, argvalues, ids=ids)
    if metafunc.function.__name__ == "test_urls":
        ids = []
        openapi = json.load(open("API/static/openapi.json"))
        argnames = ["url"]
        argvalues = []
        for endpoint, endpoint_config in openapi["paths"].items():
            argvalues.append((endpoint,))
            ids.append(f"{endpoint}")
        metafunc.parametrize(argnames, argvalues, ids=ids)


def read_valid_responses(filename):
    # read in csv or file type and return list
    # filepath = "./API/testing_validation/"
    with open(filename) as f:
        rv = json.load(f)
    return rv


def needle_in_haystack(needle, haystack):
    found = False
    for potential_needle in haystack:
        try:
            compare_dict(potential_needle, needle)
            found = True
            break
        except AssertionError:
            continue
    assert found


def compare_dict(actual, expected):
    for key in expected:
        try:
            assert key in actual
        except AssertionError:
            if len(expected[key]) > 1 or any(
                [v is not None for k, v in expected[key][0].items()]
            ):
                raise
            continue
        if isinstance(expected[key], dict):
            assert isinstance(actual[key], dict)
            compare_dict(expected[key], actual[key])
        if isinstance(expected[key], list):
            assert isinstance(actual[key], list)
            for expected_item in expected[key]:
                needle_in_haystack(expected_item, actual[key])
        else:
            if expected[key] is not None:
                assert str(expected[key]).replace("\n", " ") == str(
                    actual[key]
                ).replace("\n", " ")


response_keys = {  # API response keys where the key does not equal the index name
    # "index_name":"response_key"
    "rel_app_text_publications": "rel_app_texts",
}


def submit_query(django_client, api_key, endpoint, entity, key, data=None):
    from django.urls import reverse

    endpoint_url = reverse(f"{endpoint}-list")
    header = {"HTTP_X_API_KEY": api_key}
    responses = []
    while True:
        # query = "{}/{}".format(self.url, endpoint)
        response = django_client.get(path=endpoint_url, data=data, **header)
        if response.status_code == 429:
            wait_for = response.headers["Retry-After"]
            time.sleep(int(wait_for))
            continue
        if response.status_code != 200:
            raise Exception(response.headers)
        response_data = response.json()
        resp_key = response_keys.get(entity, entity)
        responses += response_data[resp_key]
        expected = response_data["total_hits"]
        if (response_data["count"] == 0) or (len(responses) >= expected):
            break
        after = response_data[resp_key][-1][key]
        data["o"] = json.dumps({"after": [after], "size": 1000})

    return responses


@pytest.mark.django_db
def test_operator(
    endpoint,
    q_field,
    f_field,
    key,
    entity,
    database,
    validation_sql,
    django_client,
    api_key,
):
    response_data = submit_query(
        django_client=django_client,
        endpoint=endpoint,
        data={"q": q_field, "f": f_field},
        key=key,
        entity=entity,
        api_key=api_key,
    )

    valid_response = get_db_results(database, validation_sql)
    if entity in (
        "brf_sum_texts",
        "claims",
        "draw_desc_texts",
        "detail_desc_texts",
        "brf_sum_text",
        "claim",
        "draw_desc_text",
        "detail_desc_text",
    ):
        valid_response = sorted(valid_response, key=lambda x: x["uuid"])
        response_data = sorted(response_data, key=lambda x: x["uuid"])

    assert len(valid_response) == len(response_data)
    for actual, expected in zip(response_data, valid_response):
        compare_dict(actual, expected)
    # TestCase().assertCountEqual(valid_response[endpoint], response_data)


@pytest.mark.django_db
def test_field(endpoint, q_field, f_field, key, entity, django_client, api_key):
    submit_query(
        django_client=django_client,
        endpoint=endpoint,
        data={"q": q_field, "f": f_field},
        key=key,
        entity=entity,
        api_key=api_key,
    )


@pytest.mark.django_db
def test_s_field(endpoint, q_field, s_field, key, entity, django_client, api_key):
    submit_query(
        django_client,
        endpoint=endpoint,
        data={"q": q_field, "s": s_field},
        key=key,
        entity=entity,
        api_key=api_key,
    )


@pytest.mark.django_db
def test_urls(url):
    resolve(url)


@pytest.mark.skip
def test_ascii_indexing():
    """
    Searches for names/words with accents return inconsistent results
    See "inventors.inventor_city" for document_number 20120003992 for example
    Document is missing from q={"inventors.inventor_city":"San Jose"}
    """
    assert "San Jose" == "San José"


if __name__ == "__main__":
    compare_dict(
        {
            "patent_id": "11194680",
            "patent_title": "Two node clusters recovery on a failure",
            "patent_type": "utility",
            "patent_date": "2021-12-07",
            "assignees": [{"assignee_city": "San Jose", "assignee_sequence": 0}],
            "inventors": [
                {"inventor_city": "San Jose", "inventor_sequence": 1},
                {"inventor_city": "Raleigh", "inventor_sequence": 3},
                {"inventor_city": "Seattle", "inventor_sequence": 2},
            ],
        },
        {
            "patent_id": "11194680",
            "patent_title": "Two node clusters recovery on a failure",
            "patent_date": "2021-12-07",
            "patent_type": "utility",
            "inventors": [
                {"inventor_city": None, "inventor_sequence": "0"},
                {"inventor_city": "San Jose", "inventor_sequence": "1"},
                {"inventor_city": "Raleigh", "inventor_sequence": "3"},
                {"inventor_city": "Seattle", "inventor_sequence": "2"},
            ],
            "assignees": [{"assignee_city": "San Jose", "assignee_sequence": "0"}],
        },
    )
