from __future__ import annotations

import importlib
import io
import json
import logging
import sys
import time
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def perf_records(caplog):
    return [
        json.loads(record.message)
        for record in caplog.records
        if record.name == "msd.perf"
    ]


class FakeMistConnection:
    def test_connection(self):
        return {"success": True, "org_name": "Test Org"}

    def get_sites(self):
        return [{"id": "site-1", "name": "Site 1"}]

    def get_site_health(self, site_id):
        return {
            "aps": {"total": 1, "connected": 1, "disconnected": 0, "devices": []},
            "switches": {"total": 0, "connected": 0, "disconnected": 0, "devices": []},
            "gateways": {"total": 0, "connected": 0, "disconnected": 0, "devices": []},
            "summary": {
                "total": 1,
                "connected": 1,
                "disconnected": 0,
                "health_percentage": 100.0,
            },
        }


def test_event_schema_and_json_output(caplog, monkeypatch):
    monkeypatch.delenv("PERF_MONITORING", raising=False)
    from perf_monitor import SCHEMA_FIELDS, emit_perf_event

    caplog.set_level(logging.INFO, logger="msd.perf")
    event = emit_perf_event(
        event_name="unit", module="tests", function="schema", duration_ms=1.23
    )

    assert event is not None
    records = perf_records(caplog)
    assert len(records) == 1
    assert set(SCHEMA_FIELDS).issubset(records[0])
    assert records[0]["event_name"] == "unit"
    assert records[0]["duration_ms"] == 1.23
    assert records[0]["timestamp_utc"].endswith("Z")


def test_request_hooks_log_route_event(caplog, monkeypatch):
    monkeypatch.delenv("PERF_MONITORING", raising=False)
    import app as dashboard_app

    dashboard_app._mist_connection = FakeMistConnection()
    dashboard_app.app.config["TESTING"] = True
    caplog.set_level(logging.INFO, logger="msd.perf")

    with dashboard_app.app.test_client() as client:
        assert client.get("/health").status_code == 200
        assert client.get("/").status_code == 200
        assert client.get("/api/sites?duration=1d&token=secret").status_code == 200
        assert client.get("/api/sites/site-1/health").status_code == 200

    route_events = [
        record
        for record in perf_records(caplog)
        if record["event_name"] == "flask_route"
    ]
    assert {event["route"] for event in route_events} >= {
        "/health",
        "/",
        "/api/sites",
        "/api/sites/<site_id>/health",
    }
    sites_event = next(
        event for event in route_events if event["route"] == "/api/sites"
    )
    assert sites_event["items_returned"] == 1
    assert sites_event["query_params"] == ["duration", "token"]
    assert "secret" not in json.dumps(route_events)


def test_network_processing_split_uses_response_hook(caplog, monkeypatch):
    monkeypatch.delenv("PERF_MONITORING", raising=False)
    from perf_monitor import instrument_mist_method, record_http_response

    class Worker:
        @instrument_mist_method
        def fetch(self):
            response = SimpleNamespace(
                elapsed=timedelta(milliseconds=12),
                status_code=200,
                content=b"{}",
                url="https://api.mist.com/api/v1/sites?token=secret",
                history=[],
            )
            record_http_response(response)
            return [{"id": "site-1"}]

    caplog.set_level(logging.INFO, logger="msd.perf")
    assert Worker().fetch() == [{"id": "site-1"}]

    records = perf_records(caplog)
    method_event = next(
        record for record in records if record["event_name"] == "mist_api_method"
    )
    http_event = next(
        record for record in records if record["event_name"] == "mist_api_http_call"
    )
    assert method_event["network_ms"] == 12.0
    assert method_event["call_count"] == 1
    assert method_event["processing_ms"] >= 0
    assert http_event["route"] == "/api/v1/sites"
    assert method_event["payload_bytes"] == 2
    assert "secret" not in json.dumps(records)


def test_nested_method_network_time_rolls_up(caplog, monkeypatch):
    monkeypatch.delenv("PERF_MONITORING", raising=False)
    from perf_monitor import instrument_mist_method, record_http_response

    class Worker:
        @instrument_mist_method
        def outer(self):
            return self.inner()

        @instrument_mist_method
        def inner(self):
            record_http_response(
                SimpleNamespace(
                    elapsed=timedelta(milliseconds=12),
                    status_code=200,
                    content=b"{}",
                    url="https://api.mist.com/api/v1/self",
                    history=[],
                )
            )
            return {"success": True}

    caplog.set_level(logging.INFO, logger="msd.perf")
    assert Worker().outer() == {"success": True}

    events = [
        record
        for record in perf_records(caplog)
        if record["event_name"] == "mist_api_method"
    ]
    outer_event = next(record for record in events if record["function"] == "outer")
    assert outer_event["network_ms"] == 12.0
    assert outer_event["call_count"] == 1
    assert outer_event["payload_bytes"] == 2


def test_org_level_method_does_not_log_site_id(caplog, monkeypatch):
    monkeypatch.delenv("PERF_MONITORING", raising=False)
    from perf_monitor import instrument_mist_method

    class Worker:
        @instrument_mist_method
        def get_org_sle_insights(self, sle_type):
            return {"success": True, "sle_type": sle_type}

    caplog.set_level(logging.INFO, logger="msd.perf")
    Worker().get_org_sle_insights("wifi")

    event = next(
        record
        for record in perf_records(caplog)
        if record["event_name"] == "mist_api_method"
    )
    assert event["function"] == "get_org_sle_insights"
    assert event["site_id"] is None


def test_direct_passthrough_response_does_not_raise(caplog, monkeypatch):
    monkeypatch.delenv("PERF_MONITORING", raising=False)
    from flask import g, send_file

    import app as dashboard_app

    caplog.set_level(logging.INFO, logger="msd.perf")
    with dashboard_app.app.test_request_context("/download"):
        g.perf_start = time.perf_counter()
        g.perf_trace_started = False
        response = send_file(
            io.BytesIO(b"abc"), mimetype="text/plain", download_name="x.txt"
        )
        returned = dashboard_app._log_request_perf_event(response)

    assert returned is response
    event = next(
        record
        for record in perf_records(caplog)
        if record["event_name"] == "flask_route"
    )
    assert event["payload_bytes"] == 3
    assert event["items_returned"] is None


def test_unhandled_exception_logs_error_class(caplog, monkeypatch):
    monkeypatch.delenv("PERF_MONITORING", raising=False)
    import app as dashboard_app

    original_view = dashboard_app.app.view_functions["health_check"]

    def boom():
        raise ValueError("boom")

    caplog.set_level(logging.INFO, logger="msd.perf")
    dashboard_app.app.view_functions["health_check"] = boom
    monkeypatch.setitem(dashboard_app.app.config, "TESTING", False)
    monkeypatch.setitem(dashboard_app.app.config, "PROPAGATE_EXCEPTIONS", False)
    try:
        with dashboard_app.app.test_client() as client:
            response = client.get("/health")
    finally:
        dashboard_app.app.view_functions["health_check"] = original_view

    assert response.status_code == 500
    event = next(
        record
        for record in perf_records(caplog)
        if record["event_name"] == "flask_route"
    )
    assert event["status"] == 500
    assert event["error_class"] == "ValueError"


def test_get_site_health_stage_events_with_stubbed_mistapi(caplog, monkeypatch):
    monkeypatch.delenv("PERF_MONITORING", raising=False)
    import mist_connection

    conn = mist_connection.MistConnection.__new__(mist_connection.MistConnection)
    conn.session = SimpleNamespace(_session=SimpleNamespace(hooks={}))
    conn.org_id = "org-1"

    monkeypatch.setattr(
        mist_connection.mistapi.api.v1.orgs.templates,
        "listOrgTemplates",
        lambda session, org_id: SimpleNamespace(
            data=[
                {
                    "id": "tmpl-1",
                    "deviceprofile_ids": ["dp-1"],
                    "filter_by_deviceprofile": True,
                }
            ]
        ),
    )
    monkeypatch.setattr(
        mist_connection.mistapi.api.v1.orgs.wlans,
        "listOrgWlans",
        lambda session, org_id: SimpleNamespace(
            data=[{"ssid": "Corp", "template_id": "tmpl-1", "enabled": True}]
        ),
    )
    monkeypatch.setattr(
        mist_connection.mistapi.api.v1.sites.stats,
        "listSiteDevicesStats",
        lambda session, site_id, **kwargs: SimpleNamespace(data=[]),
    )

    def fake_get_all(response, mist_session):
        assert mist_session is conn.session
        return [
            {
                "id": "ap-1",
                "type": "ap",
                "status": "connected",
                "deviceprofile_id": "dp-1",
                "port_stat": {"eth0": {"speed": 1000}},
            }
        ]

    monkeypatch.setattr(mist_connection.mistapi, "get_all", fake_get_all)

    caplog.set_level(logging.INFO, logger="msd.perf")
    health = conn.get_site_health("site-1")

    assert health["summary"]["total"] == 1
    stage_functions = {
        record["function"]
        for record in perf_records(caplog)
        if record["event_name"] == "mist_api_stage"
    }
    assert {
        "get_site_health.template_fetch",
        "get_site_health.wlan_mapping",
        "get_site_health.device_stats_fetch",
        "get_site_health.device_categorization",
        "get_site_health.summary_build",
    }.issubset(stage_functions)


def test_tracemalloc_switch_adds_peak_bytes(caplog, monkeypatch):
    monkeypatch.delenv("PERF_MONITORING", raising=False)
    monkeypatch.setenv("PERF_TRACEMALLOC", "1")
    from perf_monitor import instrument_mist_method

    class Worker:
        @instrument_mist_method
        def build(self):
            return [bytes(256) for _ in range(4)]

    caplog.set_level(logging.INFO, logger="msd.perf")
    Worker().build()
    event = next(
        record
        for record in perf_records(caplog)
        if record["event_name"] == "mist_api_method"
    )
    assert isinstance(event["peak_bytes"], int)
    assert event["peak_bytes"] > 0


def test_monitoring_off_switch_suppresses_events(caplog, monkeypatch):
    monkeypatch.setenv("PERF_MONITORING", "0")
    from perf_monitor import emit_perf_event

    caplog.set_level(logging.INFO, logger="msd.perf")
    assert emit_perf_event(event_name="off", module="tests", function="off") is None
    assert perf_records(caplog) == []


def test_benchmark_harness_smoke(monkeypatch):
    monkeypatch.delenv("PERF_MONITORING", raising=False)
    logger = logging.getLogger("msd.perf")
    logger.disabled = False
    from scripts import benchmark_routes

    benchmark_routes = importlib.reload(benchmark_routes)
    assert not logger.disabled

    results = benchmark_routes.run_benchmark(1, "small")
    assert results
    assert all(row["status"] < 500 for row in results)
    assert any(row["route"] == "/api/sites" for row in results)
