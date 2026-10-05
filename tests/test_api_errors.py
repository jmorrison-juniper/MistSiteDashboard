"""Regression tests for the API error responses (CodeQL py/stack-trace-exposure, issue #31).

Each API route must log the exception text on the server and must not send it to the client.
"""

import logging
import re
from pathlib import Path
from unittest.mock import patch

import pytest

import app as dashboard_app

INTERNAL_DETAIL = (
    "internal-detail https://api.mist.com/api/v1/private"  # Must stay in the log.
)

ROUTES_WITH_SUCCESS_FLAG = [
    ("POST", "/api/test-connection"),
    ("GET", "/api/sites"),
    ("GET", "/api/org/sle/wifi"),
    ("GET", "/api/org/sle/wifi/metric/coverage"),
    ("GET", "/api/sites/site-1/health"),
    ("GET", "/api/sites/site-1/sle"),
    ("GET", "/api/sites/site-1/devices"),
    ("GET", "/api/sites/site-1/wireless-clients"),
    ("GET", "/api/sites/site-1/wired-clients"),
    ("GET", "/api/sites/site-1/gateway-wan"),
]  # These routes send a success flag in the error body.

ROUTES_WITHOUT_SUCCESS_FLAG = [
    ("GET", "/api/sites/site-1/sle/wifi"),
    ("GET", "/api/sites/site-1/sle/impact/coverage/weak-signal"),
    ("GET", "/api/sites/site-1/sle/coverage/impacted/clients"),
    ("GET", "/api/sites/site-1/sle/wifi/csv"),
]  # These routes send the error key only.


def request_with_failing_connection(method: str, route: str):
    """Send one request while the Mist connection raises an exception with internal detail."""
    failure = RuntimeError(INTERNAL_DETAIL)  # The text that must not reach the client.
    with (
        patch.object(dashboard_app, "get_mist_connection", side_effect=failure),
        dashboard_app.app.test_client() as client,
    ):
        return client.open(route, method=method)  # The route catches the exception.


@pytest.mark.parametrize(
    ("method", "route", "expected_body"),
    [
        *[
            (
                method,
                route,
                {
                    "success": False,
                    "error": dashboard_app.ApiErrorResponse.GENERIC_MESSAGE,
                },
            )
            for method, route in ROUTES_WITH_SUCCESS_FLAG
        ],
        *[
            (method, route, {"error": dashboard_app.ApiErrorResponse.GENERIC_MESSAGE})
            for method, route in ROUTES_WITHOUT_SUCCESS_FLAG
        ],
    ],
)
def test_api_route_hides_exception_text(method, route, expected_body, caplog):
    """Make sure each route sends the generic body with status 500 and logs the detail."""
    with caplog.at_level(logging.ERROR, logger=dashboard_app.logger.name):
        response = request_with_failing_connection(method, route)
    assert (
        response.status_code == 500
    )  # The status stays the same as before the change.
    assert response.json == expected_body  # The shape stays, and the text is generic.
    assert INTERNAL_DETAIL not in response.get_data(
        as_text=True
    )  # No detail reaches the client.
    assert INTERNAL_DETAIL in caplog.text  # The operator can still read the cause.


def test_every_route_with_generic_handler_is_examined():
    """Make sure the tests above examine each route that uses the generic error handler."""
    # The module under test.
    source = Path(dashboard_app.__file__).read_text(encoding="utf-8")
    # One call for each route. Black can wrap the call after the open bracket.
    handler_calls = len(
        re.findall(r"ApiErrorResponse\.from_exception\(\s*error", source)
    )
    examined = len(ROUTES_WITH_SUCCESS_FLAG) + len(ROUTES_WITHOUT_SUCCESS_FLAG)
    assert handler_calls == examined == 14  # A new route must get a test row.
    assert "str(error)" not in source  # No route sends exception text again.


def test_error_response_logs_stack_trace(caplog):
    """Make sure the helper writes the stack trace to the server log."""
    try:
        raise ValueError(INTERNAL_DETAIL)  # A real exception with a traceback.
    except ValueError as error:
        with dashboard_app.app.app_context(), caplog.at_level(logging.ERROR):
            response, status = dashboard_app.ApiErrorResponse.from_exception(
                error, "Error fetching site %s", "site-1", include_success=False
            )
    assert status == 500
    assert response.json == {"error": dashboard_app.ApiErrorResponse.GENERIC_MESSAGE}
    assert (
        "Error fetching site site-1: " + INTERNAL_DETAIL in caplog.text
    )  # Template and values.
    assert caplog.records[-1].exc_info is not None  # The trace stays on the server.
