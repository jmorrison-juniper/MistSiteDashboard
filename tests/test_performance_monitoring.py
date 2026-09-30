from __future__ import annotations

import json
import logging
import sys
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def perf_records(caplog):
    return [json.loads(record.message) for record in caplog.records if record.name == "msd.perf"]


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
            "summary": {"total": 1, "connected": 1, "disconnected": 0, "health_percentage": 100.0},
        }


def test_event_schema_and_json_output(caplog, monkeypatch):
    monkeypatch.delenv("PERF_MONITORING", raising=False)
    from perf_monitor import SCHEMA_FIELDS, emit_perf_event

    caplog.set_level(logging.INFO, logger="msd.perf")
    event = emit_perf_event(event_name="unit", module="tests", function="schema", duration_ms=1.23)

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

    route_events = [record for record in perf_records(caplog) if record["event_name"] == "flask_route"]
    assert {event["route"] for event in route_events} >= {"/health", "/", "/api/sites", "/api/sites/<site_id>/health"}
    sites_event = next(event for event in route_events if event["route"] == "/api/sites")
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
    method_event = next(record for record in records if record["event_name"] == "mist_api_method")
    http_event = next(record for record in records if record["event_name"] == "mist_api_http_call")
    assert method_event["network_ms"] == 12.0
    assert method_event["call_count"] == 1
    assert method_event["processing_ms"] >= 0
    assert http_event["route"] == "/api/v1/sites"
    assert "secret" not in json.dumps(records)


def test_get_site_health_stage_events_with_stubbed_mistapi(caplog, monkeypatch):
    monkeypatch.delenv("PERF_MONITORING", raising=False)
    import mist_connection

    conn = mist_connection.MistConnection.__new__(mist_connection.MistConnection)
    conn.session = SimpleNamespace(_session=SimpleNamespace(hooks={}))
    conn.org_id = "org-1"

    monkeypatch.setattr(
        mist_connection.mistapi.api.v1.orgs.templates,
        "listOrgTemplates",
        lambda session, org_id: SimpleNamespace(data=[{"id": "tmpl-1", "deviceprofile_ids": ["dp-1"], "filter_by_deviceprofile": True}]),
    )
    monkeypatch.setattr(
        mist_connection.mistapi.api.v1.orgs.wlans,
        "listOrgWlans",
        lambda session, org_id: SimpleNamespace(data=[{"ssid": "Corp", "template_id": "tmpl-1", "enabled": True}]),
    )
    monkeypatch.setattr(
        mist_connection.mistapi.api.v1.sites.stats,
        "listSiteDevicesStats",
        lambda session, site_id, **kwargs: SimpleNamespace(data=[]),
    )
    monkeypatch.setattr(
        mist_connection.mistapi,
        "get_all",
        lambda response, mist_session: [
            {"id": "ap-1", "type": "ap", "status": "connected", "deviceprofile_id": "dp-1", "port_stat": {"eth0": {"speed": 1000}}}
        ],
    )

    caplog.set_level(logging.INFO, logger="msd.perf")
    health = conn.get_site_health("site-1")

    assert health["summary"]["total"] == 1
    stage_functions = {record["function"] for record in perf_records(caplog) if record["event_name"] == "mist_api_stage"}
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
    event = next(record for record in perf_records(caplog) if record["event_name"] == "mist_api_method")
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
    from scripts.benchmark_routes import run_benchmark

    results = run_benchmark(1, "small")
    assert results
    assert all(row["status"] < 500 for row in results)
    assert any(row["route"] == "/api/sites" for row in results)
