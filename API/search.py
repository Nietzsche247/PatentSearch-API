import logging

from elasticsearch import Elasticsearch

from API.exceptions import SearchTimeoutError


class PatentsViewElasticSearch:
    def __init__(self, host, port, username, password, timeout):
        hoststring = "{host}:{port}".format(host=host, port=port)
        self.logger = logging.getLogger("API")
        if username is not None:
            self.es = Elasticsearch(
                hosts=hoststring,
                basic_auth=(username, password),
                request_timeout=timeout,
            )
            self.logger.debug("Connecting with credentials")
        else:
            self.logger.debug("Connecting without credentials")
            self.es = Elasticsearch(hosts=hoststring, request_timeout=timeout)
        self.logger.info(f"Connecting to {hoststring}")

    @classmethod
    def from_django_settings(cls):
        from django.conf import settings

        config = settings.ELASTICSEARCH["default"]
        return cls(
            host=config["host"],
            port=config["port"],
            username=config.get("username", None),
            password=config.get("password"),
            timeout=int(config["timeout"]),
        )

    def bulk_load_es_documents(self, documents, load_config):
        responses = []
        target_index = load_config["index"]
        indexing_batch_size = load_config["indexing_batch_size"]
        id_field = load_config["id_field"]
        current_batch_size = 0
        action_data_pairs = []
        for data_row in documents:
            if current_batch_size >= indexing_batch_size:
                current_batch_size = 0
                r = self.es.bulk(action_data_pairs)
                responses.append(r)
                action_data_pairs = []
            action_data_pairs.append(
                {"create": {"_id": data_row[id_field], "_index": target_index}}
            )
            action_data_pairs.append(data_row)
            current_batch_size += 2
        r = self.es.bulk(action_data_pairs)
        responses.append(r)
        return responses

    def count(self, index, query):
        count_body = {}
        if "query" in query:
            count_body["query"] = query["query"]
        else:
            count_body["query"] = query
        results = self.es.count(index=index, body=count_body)
        if "timed_out" in results and results["timed_out"]:
            raise SearchTimeoutError("Search timed out")
        return results

    def search(self, index, query, fields, size, offset, sort):
        search_args = {
            "index": index,
            "body": query,
            "sort": sort,
            "size": size,
            "_source": fields,
        }
        if offset:
            search_args["search_after"] = offset
        results = self.es.search(**search_args)
        if results["timed_out"]:
            raise SearchTimeoutError("Search timed out")
        return results


def get_searcher():
    """Backend seam (Lapse): settings.LAPSE_BACKEND = "sqlite" serves from API.search_sqlite, else Elasticsearch."""
    from django.conf import settings

    if getattr(settings, "LAPSE_BACKEND", "elasticsearch") == "sqlite":
        from API.search_sqlite import LapseSQLiteSearch

        return LapseSQLiteSearch.from_django_settings()
    return PatentsViewElasticSearch.from_django_settings()
