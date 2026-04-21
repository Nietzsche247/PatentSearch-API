class APIResponseDocument:
    def __init__(self, error: bool, count: int, total_hits: int):
        """
        A Parent class that templates the fields to be included in every API response
        :param error: Boolean indicator for API error
        :param count: Number of records returned for the current request
        :param total_hits: Total number of records that satisfy the query
        """
        self.total_hits = total_hits
        self.count = count
        self.error = error


class APIDataContainer:
    def __init__(self, *args, **kwargs):
        fields = kwargs.get("fields")
        for field in fields:
            if field in kwargs:
                self.__setattr__(field, kwargs.get(field))
        # self.patent_number = kwargs.get('patent_number', None)
        # self.cited_patent_number = kwargs.get('cited_patent_number', None)
        # self.citation_category = kwargs.get('citation_category', None)
        # self.citation_date = kwargs.get('citation_date', None)
        # self.citation_sequence = kwargs.get('citation_sequence', None)
        # self.cited_patent_details = kwargs.get(' cited_patent_details', None)
