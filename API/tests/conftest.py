import logging
import os

import pytest
from es_data_load.DataSources import MySQLDataSource
from es_data_load.es import ElasticsearchWrapper
from es_data_load.pv.mappings.PVMappingsManager import PVLoadConfiguration
from es_data_load.pv.schemas.PVSchemaManager import PVSchemaManager
from es_data_load.specification import LoadJob
from pymysql import connect as pymysql_connect

from API.search import PatentsViewElasticSearch

logger = logging.getLogger()


def load_staging_data(host, port):
    es_logger = logging.getLogger("es-data-load")

    ch = logging.StreamHandler()
    ch.setLevel(logging.INFO)

    es_logger.addHandler(ch)
    es_logger.setLevel(logging.INFO)
    logger.info("Loading test data")
    search_wrapper = ElasticsearchWrapper(hoststring=f"http://{host}:{port}")

    schema_manager = PVSchemaManager.load_default_pv_schema(
        search_wrapper=search_wrapper, suffix=""
    )
    schema_manager.create_es_indices()

    mysql_source = MySQLDataSource(
        connection=pymysql_connect(
            host=os.getenv("INGEST_HOST"),
            user=os.getenv("INGEST_USER"),
            password=os.getenv("INGEST_PASSWORD"),
            defer_connect=True,
            port=int(os.getenv("INGEST_PORT")),
        )
    )

    loadconfigs = PVLoadConfiguration.load_default_pv_configuration(
        suffix="",
        elastic_source_patent="elastic_production_patent_20230930",
        elastic_source_pregrant="elastic_production_pgpub_20230930",
        reporting_source_patent="elastic_staging",
    )

    load_job = LoadJob(loadconfigs, mysql_source, search_wrapper=search_wrapper, test=0)
    try:
        load_job.process_all_load_operations()
    except Exception as e:
        print(e)
        raise
    for name in loadconfigs.get_load_operation_names():
        logger.info(load_job.get_load_results(name))


# @pytest.fixture(scope="session")
# def es_container(request):
#     logger.info("Starting Elasticsearch container")
#     es = PersistentElasticSearchContainer("elasticsearch:8.11.3", port_to_expose=9200)
#     es = es.start()
#     load_staging_data(
#         es.get_container_host_ip(), es.get_exposed_port(es.port_to_expose)
#     )

#     yield es
#
# def close_es():
#     logger.info("Stopping Elasticsearch container")
#
#     es.stop()
#
# request.addfinalizer(close_es)


# @pytest.fixture(scope="session")
# def django_settings():
#     from django.conf import settings

#     settings.ELASTICSEARCH = {
#         "default": {
#             "host": "http://localhost",
#             "port": 9200,
#             "timeout": 60,
#         },
#     }
#     yield settings


@pytest.fixture()
def api_key():
    from API.models import APIUserKey

    _, key = APIUserKey.objects.create_key(name="my-remote-service")
    return key


@pytest.fixture()
def django_client(settings):
    from django.test import Client

    client = Client()
    return client


@pytest.fixture()
def test_search_wrapper(settings):
    """
    Elasticsearch credentials derived from test elasticsearch docker container
    :param django_settings:
    """
    yield PatentsViewElasticSearch.from_django_settings()


@pytest.fixture()
def default_search_wrapper(settings):
    """
    Use default Elasticsearch credentials from settings.py file for connection.
    Used when elastic data/indices need to be verified
    :param settings:
    """
    yield PatentsViewElasticSearch.from_django_settings()
