import logging
import re

import elasticsearch

from API.UsageLogging import log_usage


class SearchTimeoutError(Exception):
    pass


class InvalidJSONFormatError(Exception):
    pass


class InvalidQueryStringError(Exception):
    pass


class InvalidPagingException(Exception):
    pass


class InvalidFieldException(Exception):
    pass


class InvalidOptionException(Exception):
    pass


def custom_exception_handler(exc, context):
    import traceback

    from rest_framework import status
    from rest_framework.exceptions import ParseError
    from rest_framework.response import Response
    from rest_framework.views import exception_handler

    logger = logging.getLogger("API")
    logger.debug("".join(traceback.format_tb(exc.__traceback__)))
    logger.debug(str(exc))
    if exc.__class__ in [
        InvalidFieldException,
        InvalidQueryStringError,
        InvalidJSONFormatError,
        ParseError,
        InvalidPagingException,
        InvalidOptionException,
    ]:
        response = Response(
            status=status.HTTP_400_BAD_REQUEST,
            headers={"X-Status-Reason": str(exc), "X-Status-Reason-Code": "ERR_Q"},
            data={"error": True},
        )
    elif (
        isinstance(exc, AttributeError)
        and exc.args[0] == "'list' object has no attribute 'keys'"
    ):
        response = Response(
            status=status.HTTP_400_BAD_REQUEST,
            headers={
                "X-Status-Reason": "Invalid API Query Syntax (JSON rules not violated)",
                "X-Status-Reason-Code": "ERR_Q",
            },
            data={"error": True},
        )
    elif (
        issubclass(exc.__class__, elasticsearch.ApiError)
        or issubclass(exc.__class__, elasticsearch.TransportError)
        or isinstance(exc, TimeoutError)
    ):
        stat = status.HTTP_500_INTERNAL_SERVER_ERROR
        status_reason = "Internal Server Error"
        status_reason_code = "ERR_ES"
        try:
            error_capsule = exc.info["error"]["root_cause"][0]
            es_error_type = error_capsule["type"]
            if es_error_type == "query_shard_exception":
                error_capsule = exc.info["error"]["failed_shards"][0]["reason"][
                    "caused_by"
                ]
                es_error_type = error_capsule["type"]
            reason = error_capsule["reason"]
            if es_error_type == "number_format_exception":
                stat = status.HTTP_400_BAD_REQUEST
                status_reason_code = "ERR_Q"
                status_reason = "Invalid number supplied {reason}".format(reason=reason)
            if es_error_type == "parse_exception":
                reason = exc.info["error"]["root_cause"][0]["reason"]
                logger.debug(reason)
                match = re.match("failed to parse date.*", reason)
                if match:
                    stat = status.HTTP_400_BAD_REQUEST
                    status_reason_code = "ERR_Q"
                    status_reason = "Invalid date supplied in query"
            if es_error_type == "illegal_argument_exception":
                stat = status.HTTP_400_BAD_REQUEST
                status_reason_code = "ERR_Q"
                # status_reason = reason
        except KeyError:
            # Key error here means the error response from elastic search didn't have the things we expected it to have
            # so we default to generic 500
            pass
        response = Response(
            status=stat,
            headers={
                "X-Status-Reason": status_reason,
                "X-Status-Reason-Code": status_reason_code,
            },
            data={"error": True},
        )
    else:
        response = exception_handler(exc, context)
    try:
        log_usage(
            context["request"],
            "error",
            response.status_code,
            exception=1,
            fields_involved=[],
        )
    except AttributeError:
        log_usage(context["request"], "error", -1, exception=1, fields_involved=[])
    return response
