def test_es_connection(default_search_wrapper):
    print(default_search_wrapper.es.info())
