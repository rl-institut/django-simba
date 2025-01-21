import logging


class FilterStatusCode(logging.Filter):
    def __init__(self, status_code: int, search_text: str):
        super().__init__()
        self.status_code = status_code
        self.search_text = search_text

    def filter(self, record):
        if vars(record).get("status_code", None) != self.status_code:
            # Other status codes dont get filtered
            return True
        # If the search text is not found, return True.
        # The record will not be filtered out
        not_found = self.search_text not in record.args[0]
        return not_found
