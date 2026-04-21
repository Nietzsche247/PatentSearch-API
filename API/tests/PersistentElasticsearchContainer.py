from testcontainers.elasticsearch import ElasticSearchContainer


class PersistentElasticSearchContainer(ElasticSearchContainer):
    def __init__(self, image="elasticsearch", port_to_expose=9200, **kwargs):
        super().__init__(image=image, port_to_expose=port_to_expose, **kwargs)
        self.persistent_container = kwargs.get("persistent_container", False)

    def __del__(self):
        if not self.persistent_container:
            """
            Try to remove the container in all circumstances
            """
            if self._container is not None:
                try:
                    self.stop()
                except:  # noqa: E722
                    pass
