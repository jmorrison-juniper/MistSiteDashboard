"""Offline regression tests for Mist response normalization and failure boundaries."""

import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import mist_connection


class ApiHarness:
    def __init__(self, monkeypatch):
        monkeypatch.delenv("MIST_APITOKEN", raising=False)
        monkeypatch.setenv("PERF_MONITORING", "0")
        self.api = MagicMock()
        monkeypatch.setattr(mist_connection.mistapi, "api", self.api)
        monkeypatch.setattr(
            mist_connection.mistapi,
            "APISession",
            MagicMock(side_effect=AssertionError("Live API sessions are forbidden")),
        )
        monkeypatch.setattr(
            mist_connection.mistapi, "get_all", lambda **kwargs: kwargs["response"].data
        )
        self.connection = mist_connection.MistConnection()
        self.connection.org_id = "org"
        self.session = MagicMock()
        monkeypatch.setattr(self.connection, "_get_session", lambda: self.session)
        monkeypatch.setattr(mist_connection.time, "time", lambda: 10000)
        self.sleep = MagicMock()
        monkeypatch.setattr(mist_connection.time, "sleep", self.sleep)

    def endpoint(self, path, data=None, error=None):
        endpoint = self.api.v1
        for part in path.split("."):
            endpoint = getattr(endpoint, part)
        endpoint.return_value = SimpleNamespace(data=data)
        endpoint.side_effect = error
        return endpoint


@pytest.fixture
def api(monkeypatch):
    return ApiHarness(monkeypatch)


def test_site_health_mapping_counts_and_unknown_devices(api):
    api.endpoint(
        "orgs.templates.listOrgTemplates",
        [
            {"id": "t", "deviceprofile_ids": ["p"], "filter_by_deviceprofile": True},
            None,
        ],
    )
    api.endpoint(
        "orgs.wlans.listOrgWlans",
        [
            {"ssid": "Corp", "template_id": "t"},
            {"ssid": "Corp", "template_id": "t"},
            {"ssid": "Disabled", "template_id": "t", "enabled": False},
            None,
        ],
    )
    stats = api.endpoint(
        "sites.stats.listSiteDevicesStats",
        [
            {
                "type": "ap",
                "status": "connected",
                "deviceprofile_id": "p",
                "num_clients": None,
            },
            {
                "type": "switch",
                "clients_stats": {"total": {"num_aps": [2], "num_wired_clients": None}},
            },
            {"type": "gateway", "status": "connected"},
            {"type": "unknown", "status": "connected"},
        ],
    )
    result = api.connection.get_site_health("site")
    assert result["summary"] == {
        "total": 4,
        "connected": 3,
        "disconnected": 1,
        "health_percentage": 75.0,
    }
    assert result["aps"]["devices"][0]["ssids"] == ["Corp"]
    assert result["aps"]["devices"][0]["num_clients"] == 0
    assert result["switches"]["devices"][0]["num_aps"] == 2
    assert result["switches"]["disconnected"] == 1
    stats.assert_called_once_with(api.session, "site", type="all", limit=1000)


def test_site_health_mapping_failure_is_nonfatal(api):
    api.endpoint("orgs.templates.listOrgTemplates", error=RuntimeError("mapping"))
    api.endpoint("sites.stats.listSiteDevicesStats", [])
    assert api.connection.get_site_health("site")["summary"]["health_percentage"] == 0


@pytest.mark.parametrize(
    "duration,query",
    [
        ("10m", {"start": "9400", "end": "10000"}),
        ("today", {"duration": "1d"}),
        ("invalid", {"duration": "1d"}),
    ],
)
def test_site_sle_samples_duplicates_and_time_range(api, duration, query):
    api.endpoint(
        "sites.sle.listSiteSlesMetrics",
        {"enabled": ["coverage-v2", "coverage-v4", "capacity", "other"]},
    )
    trend = api.endpoint("sites.sle.getSiteSleSummaryTrend")
    trend.side_effect = [
        SimpleNamespace(
            data={"sle": {"samples": {"total": [100, None], "degraded": [25, None]}}}
        ),
        SimpleNamespace(data={"sle": {"samples": {"total": [100], "degraded": [90]}}}),
        SimpleNamespace(data={"sle": {"samples": {"total": [0], "degraded": [0]}}}),
    ]
    result = api.connection.get_site_sle("site", duration)
    assert result == {
        "wifi": {"metrics": {"coverage": 75.0}, "available": True},
        "wired": {"metrics": {}, "available": False},
        "wan": {"metrics": {}, "available": False},
    }
    assert all(
        all(call.kwargs[key] == value for key, value in query.items())
        for call in trend.call_args_list
    )


def test_sle_details_classifier_impact_sorting_and_version(api):
    api.endpoint("sites.sle.listSiteSlesMetrics", {"enabled": ["coverage-v2"]})
    trend = api.endpoint(
        "sites.sle.getSiteSleSummaryTrend",
        {
            "sle": {"samples": {"total": [100, None], "degraded": [25]}},
            "impact": {"num_users": 4},
            "classifiers": [
                {"name": "small", "samples": {"degraded": [1, None]}},
                {"name": "large", "samples": {"degraded": [3]}},
                {"name": "zero", "samples": {"degraded": [0]}},
                None,
            ],
        },
    )
    api.endpoint(
        "sites.sle.getSiteSleSummary",
        {
            "classifiers": [
                {"name": "large", "impact": {"num_aps": 2, "total_users": None}},
                {},
                None,
            ]
        },
    )
    result = api.connection.get_sle_details("site", "wifi")
    coverage = result["metrics"]["coverage"]
    assert result["category"] == "wifi" and result["duration"] == "1d"
    assert coverage["sle_value"] == 75.0 and coverage["impact"] == {"num_users": 4}
    assert [item["percentage"] for item in coverage["classifiers"]] == [75.0, 25.0]
    assert coverage["classifiers"][0]["impact"] == {
        "num_aps": 2,
        "total_aps": 0,
        "num_gateways": 0,
        "total_gateways": 0,
        "num_switches": 0,
        "total_switches": 0,
        "num_users": 0,
        "total_users": 0,
    }
    assert trend.call_args.kwargs["metric"] == "coverage-v2"


def test_sle_details_optional_impact_failure_and_invalid_category(api):
    api.endpoint("sites.sle.listSiteSlesMetrics", {"enabled": ["coverage"]})
    api.endpoint(
        "sites.sle.getSiteSleSummaryTrend", {"classifiers": [{"name": "zero"}]}
    )
    api.endpoint("sites.sle.getSiteSleSummary", error=RuntimeError("optional"))
    assert api.connection.get_sle_details("site", "wifi")["metrics"]["coverage"] == {
        "name": "coverage",
        "sle_value": None,
        "classifiers": [],
        "impact": {},
    }
    with pytest.raises(ValueError, match="Invalid category"):
        api.connection.get_sle_details("site", "invalid")


@pytest.mark.parametrize(
    "metric,wired", [("coverage", False), ("switch-health-v2", True)]
)
def test_classifier_impact_normalization(api, metric, wired):
    endpoint = api.endpoint(
        "sites.sle.getSiteSleImpactSummary",
        {
            "ap": [
                {"ap_mac": "a", "degraded": 1},
                {"ap_mac": "b", "degraded": 3},
                {"degraded": 0},
            ],
            "wlan": [{"wlan_id": "w", "degraded": 1}],
            "device_type": [{"device_type": "laptop", "degraded": 1}],
            "device_os": [{"name": "Linux", "degraded": 1}],
            "band": [
                {"band": "24", "degraded": 1},
                {"band": "5", "degraded": 2},
                {"band": "6", "degraded": 3},
            ],
            "switch": [{"mac": "s", "duration": 2}],
            "chassis": [{"chassis": "0", "duration": 1}],
        },
    )
    result = api.connection.get_classifier_impact_details("site", metric, "bad")
    assert [item["mac"] for item in result["aps"]] == ["b", "a"]
    assert [item["band"] for item in result["bands"]] == ["6 GHz", "5 GHz", "2.4 GHz"]
    assert result["wlans"][0]["id"] == "w"
    assert result["device_types"][0]["type"] == "laptop"
    assert result["device_os"][0]["os"] == "Linux"
    assert result["switches"][0]["mac"] == "s"
    assert result["chassis"][0]["chassis_id"] == "0"
    assert ("fields" in endpoint.call_args.kwargs) is wired


@pytest.mark.parametrize("data", [None, [], {"ap": [None]}])
def test_classifier_impact_invalid_response_returns_empty(api, data):
    api.endpoint("sites.sle.getSiteSleImpactSummary", data)
    assert api.connection.get_classifier_impact_details("site", "coverage", "bad") == {
        key: []
        for key in (
            "aps",
            "wlans",
            "device_types",
            "device_os",
            "bands",
            "switches",
            "chassis",
        )
    }


def test_wireless_clients_merge_precedence_and_session_order(api):
    api.endpoint(
        "sites.stats.listSiteWirelessClientsStats",
        [{"mac": "a", "hostname": "live", "last_seen": 10}, {}],
    )
    api.endpoint(
        "sites.clients.searchSiteWirelessClients",
        [
            {"mac": "a", "last_hostname": "old", "last_ip": "ip"},
            {"mac": "b", "last_hostname": "search"},
        ],
    )
    api.endpoint(
        "sites.clients.searchSiteWirelessClientSessions",
        [
            {"mac": "a", "connect": 1, "disconnect": 20, "ssid": "Corp"},
            {"mac": "a", "connect": 2, "disconnect": 15, "duration": 7},
            {"mac": "c", "disconnect": 5, "client_manufacture": "vendor"},
        ],
    )
    clients = api.connection.get_wireless_client_sessions("site")
    assert [client["mac"] for client in clients] == ["a", "b", "c"]
    assert clients[0]["hostname"] == "live" and clients[0]["ip"] == "ip"
    assert clients[0]["is_connected"] is True
    assert (
        clients[0]["last_seen"],
        clients[0]["connect"],
        clients[0]["disconnect"],
    ) == (20, 1, 15)
    assert clients[1]["is_connected"] is False and clients[2]["manufacture"] == "vendor"


@pytest.mark.parametrize(
    "timestamp,expected",
    [("9701.5", True), ("9700", False), ("bad", False), ("", False)],
)
def test_wired_clients_timestamp_boundary_and_fallbacks(api, timestamp, expected):
    api.endpoint(
        "sites.wired_clients.searchSiteWiredClients",
        [
            {
                "mac": "a",
                "timestamp": timestamp,
                "ip": [],
                "dhcp_fqdn": "host",
                "device_mac_port": [
                    {"ip": "ip", "device_mac": "switch", "start": "bad", "port_id": "p"}
                ],
            },
            {
                "mac": "b",
                "ip": "string",
                "device_mac": ["fallback"],
                "device_mac_port": ["invalid"],
            },
        ],
    )
    clients = api.connection.get_wired_clients("site")
    assert clients[0]["is_connected"] is expected
    assert clients[0]["ip"] == "ip" and clients[0]["hostname"] == "host"
    assert clients[0]["switch_mac"] == "switch" and clients[0]["port_id"] == "p"
    assert clients[1]["ip"] == "string" and clients[1]["switch_mac"] == "fallback"


def test_gateway_wan_filter_and_peer_failure_isolation(api):
    api.endpoint(
        "sites.stats.listSiteDevicesStats",
        [
            {
                "mac": "gateway",
                "if_stat": {
                    "lan": {"port_usage": "lan"},
                    "invalid": None,
                    "wan": {"port_usage": "wan", "up": True, "ips": ["one", "two"]},
                    "lte": {"wan_type": "lte", "ips": "not-list"},
                },
            }
        ],
    )
    api.endpoint("orgs.stats.searchOrgPeerPathStats", error=RuntimeError("VPN"))
    bgp = api.endpoint(
        "orgs.stats.searchOrgBgpStats", {"results": [{"neighbor": "peer", "up": True}]}
    )
    result = api.connection.get_gateway_wan_status("site")[0]
    assert [
        (port["name"], port["ip"], port["status"]) for port in result["wan_ports"]
    ] == [
        ("wan", "one", "up"),
        ("lte", "", "down"),
    ]
    assert result["vpn_peers"] == [] and result["bgp_peers"][0]["neighbor"] == "peer"
    bgp.assert_called_once_with(
        api.session, "org", site_id="site", mac="gateway", limit=100
    )


@pytest.mark.parametrize(
    "method", ["get_org_sle_insights", "get_org_worst_sites_by_metric"]
)
@pytest.mark.parametrize("wrapped", [True, False])
def test_org_insights_pagination_retry_query_and_limit(api, method, wrapped):
    sites = api.endpoint("orgs.sites.listOrgSites")
    sites.side_effect = [
        SimpleNamespace(
            data=[{"id": str(index), "name": str(index)} for index in range(1000)]
        ),
        SimpleNamespace(data=[{"id": "known", "name": "Known"}]),
    ]
    results = [{"site_id": "known", "coverage": 0.5}, {"site_id": "unknown"}]
    api.session.mist_get.side_effect = [
        SimpleNamespace(status_code=400),
        SimpleNamespace(status_code=400),
        SimpleNamespace(
            status_code=200, data={"results": results} if wrapped else results
        ),
    ]
    argument = "wifi" if method == "get_org_sle_insights" else "coverage"
    result = getattr(api.connection, method)(argument, "1h", limit=1)
    assert result["success"] is True and result["total_sites"] == 1
    assert result["sites"] == [
        {"site_id": "known", "site_name": "Known", "coverage": 0.5}
    ]
    assert sites.call_args.kwargs["page"] == 2
    assert [call.args[0] for call in api.sleep.call_args_list] == [1, 2]
    query = api.session.mist_get.call_args.kwargs["query"]
    assert query == {
        "sle": "ap-availability" if method == "get_org_sle_insights" else "coverage",
        "start": "-76400" if method == "get_org_sle_insights" else "6400",
        "end": "10000",
        "limit": "1",
    }


@pytest.mark.parametrize(
    "method,argument",
    [
        ("get_org_sle_insights", "wifi"),
        ("get_org_worst_sites_by_metric", "unknown"),
    ],
)
def test_org_insights_non_200_and_exception_contract(api, method, argument):
    api.endpoint("orgs.sites.listOrgSites", [])
    api.session.mist_get.return_value = SimpleNamespace(status_code=500)
    result = getattr(api.connection, method)(argument)
    assert result["success"] is True and result["sites"] == []
    api.session.mist_get.side_effect = RuntimeError("failure")
    result = getattr(api.connection, method)(argument)
    assert result["success"] is False and result["error"] == "failure"


@pytest.mark.parametrize(
    "method",
    [
        "get_site_health",
        "get_site_sle",
        "get_sle_details",
        "get_wireless_client_sessions",
        "get_wired_clients",
        "get_gateway_wan_status",
    ],
)
def test_session_failure_propagates(api, monkeypatch, method):
    monkeypatch.setattr(
        api.connection, "_get_session", MagicMock(side_effect=RuntimeError("session"))
    )
    arguments = ("site", "wifi") if method == "get_sle_details" else ("site",)
    with pytest.raises(RuntimeError, match="session"):
        getattr(api.connection, method)(*arguments)


def test_source_failures_and_org_discovery(api, monkeypatch):
    api.endpoint(
        "sites.stats.listSiteWirelessClientsStats", error=RuntimeError("stats")
    )
    api.endpoint(
        "sites.clients.searchSiteWirelessClients", error=RuntimeError("search")
    )
    api.endpoint("sites.clients.searchSiteWirelessClientSessions", [])
    api.endpoint(
        "sites.wired_clients.searchSiteWiredClients", error=RuntimeError("wired")
    )
    assert api.connection.get_wireless_client_sessions("site") == []
    assert api.connection.get_wired_clients("site") == []
    api.connection.org_id = None
    monkeypatch.setattr(api.connection, "test_connection", lambda: {"success": False})
    assert (
        api.connection.get_org_sle_insights("wifi")["error"]
        == "Could not determine organization ID"
    )
    assert api.connection.get_org_worst_sites_by_metric("coverage")["success"] is False
    with pytest.raises(ValueError, match="organization ID"):
        api.connection.get_gateway_wan_status("site")
    assert api.connection.get_org_sle_insights("invalid")["success"] is False


def test_site_health_device_failure_propagates(api):
    api.endpoint("orgs.templates.listOrgTemplates", [])
    api.endpoint("orgs.wlans.listOrgWlans", [])
    api.endpoint("sites.stats.listSiteDevicesStats", error=RuntimeError("devices"))
    with pytest.raises(RuntimeError, match="devices"):
        api.connection.get_site_health("site")


@pytest.mark.parametrize("method", ["get_site_sle", "get_sle_details"])
def test_sle_enabled_metrics_failure_returns_empty(api, method):
    api.endpoint("sites.sle.listSiteSlesMetrics", error=RuntimeError("metrics"))
    arguments = ("site", "wifi") if method == "get_sle_details" else ("site",)
    result = getattr(api.connection, method)(*arguments)
    if method == "get_sle_details":
        assert result == {"category": "wifi", "duration": "1d", "metrics": {}}
    else:
        assert result == {
            key: {"metrics": {}, "available": False} for key in ("wifi", "wired", "wan")
        }


def test_sle_metric_failure_keeps_other_metrics(api):
    api.endpoint("sites.sle.listSiteSlesMetrics", {"enabled": ["coverage", "capacity"]})
    trend = api.endpoint("sites.sle.getSiteSleSummaryTrend")
    trend.side_effect = [
        RuntimeError("coverage"),
        SimpleNamespace(data={"sle": {"samples": {"total": [10], "degraded": [2]}}}),
    ]
    api.endpoint("sites.sle.getSiteSleSummary", error=RuntimeError("impact"))
    result = api.connection.get_sle_details("site", "wifi")["metrics"]
    assert result["coverage"] == {
        "name": "coverage",
        "sle_value": None,
        "classifiers": [],
        "impact": {},
    }
    assert result["capacity"]["sle_value"] == 80.0
    trend.side_effect = [
        RuntimeError("coverage"),
        {"sle": {"samples": {"total": [10]}}},
    ]
    assert api.connection.get_site_sle("site")["wifi"]["metrics"] == {"capacity": 100.0}


def test_sle_raw_response_and_optional_classifier_impact_failure(api):
    metrics = api.endpoint("sites.sle.listSiteSlesMetrics")
    metrics.return_value = {"enabled": ["coverage"]}
    trend = api.endpoint("sites.sle.getSiteSleSummaryTrend")
    trend.return_value = {
        "sle": {"samples": {"total": [10], "degraded": [None, 1]}},
        "classifiers": [{"name": "one", "samples": {"degraded": [1]}}],
    }
    api.endpoint("sites.sle.getSiteSleSummary", error=RuntimeError("optional"))
    result = api.connection.get_sle_details("site", "wifi")["metrics"]["coverage"]
    assert result["sle_value"] == 90.0
    assert result["classifiers"][0]["percentage"] == 100.0
    assert set(result["classifiers"][0]["impact"].values()) == {0}


def test_wired_duplicate_mac_and_partial_malformed_source(api):
    api.endpoint(
        "sites.wired_clients.searchSiteWiredClients",
        [
            {"mac": "a", "timestamp": "9800", "ip": ["first"]},
            {
                "mac": "a",
                "timestamp": "-1",
                "ip": "",
                "device_mac_port": [{"ip": "not-used"}],
            },
            None,
            {"mac": "never-processed"},
        ],
    )
    clients = api.connection.get_wired_clients("site")
    assert len(clients) == 1
    assert clients[0]["ip"] == "" and clients[0]["connected_time"] == 0
    assert clients[0]["last_seen"] == -1 and clients[0]["is_connected"] is False


def test_wireless_partial_source_failure_continues_next_source(api):
    api.endpoint(
        "sites.stats.listSiteWirelessClientsStats",
        [
            {"mac": "a", "hostname": "first"},
            {"mac": "a", "hostname": "last"},
            None,
        ],
    )
    api.endpoint(
        "sites.clients.searchSiteWirelessClients",
        [
            {"mac": "a", "last_ip": "ip"},
            None,
        ],
    )
    api.endpoint(
        "sites.clients.searchSiteWirelessClientSessions",
        [
            {"mac": "a", "disconnect": 20},
            {"mac": "b"},
            None,
        ],
    )
    clients = api.connection.get_wireless_client_sessions("site")
    assert [client["mac"] for client in clients] == ["a", "b"]
    assert clients[0]["hostname"] == "last" and clients[0]["ip"] == "ip"
    assert clients[0]["disconnect"] == 20 and clients[0]["is_connected"] is True


@pytest.mark.parametrize("data", [None, [], {"results": []}])
def test_gateway_peer_response_shapes_and_defaults(api, data):
    api.endpoint("sites.stats.listSiteDevicesStats", [{}])
    api.endpoint("orgs.stats.searchOrgPeerPathStats", data)
    api.endpoint("orgs.stats.searchOrgBgpStats", data)
    result = api.connection.get_gateway_wan_status("site")[0]
    assert result["name"] == "Unknown" and result["mac"] == ""
    assert result["wan_ports"] == result["vpn_peers"] == result["bgp_peers"] == []


def test_gateway_partial_peer_failure_preserves_other_peers(api):
    api.endpoint("sites.stats.listSiteDevicesStats", [{"mac": "gateway"}])
    api.endpoint(
        "orgs.stats.searchOrgPeerPathStats", {"results": [{"vpn_name": "VPN"}, None]}
    )
    api.endpoint("orgs.stats.searchOrgBgpStats", error=RuntimeError("BGP"))
    result = api.connection.get_gateway_wan_status("site")[0]
    assert result["vpn_peers"][0]["vpn_name"] == "VPN"
    assert result["vpn_peers"][0]["up"] is False and result["bgp_peers"] == []


def test_gateway_device_failure_propagates(api):
    api.endpoint("sites.stats.listSiteDevicesStats", error=RuntimeError("gateways"))
    with pytest.raises(RuntimeError, match="gateways"):
        api.connection.get_gateway_wan_status("site")


@pytest.mark.parametrize(
    "method,argument",
    [
        ("get_org_sle_insights", "wan"),
        ("get_org_worst_sites_by_metric", "switch-stc"),
    ],
)
@pytest.mark.parametrize("response", [None, SimpleNamespace(status_code=400)])
def test_org_retry_exhaustion_and_missing_response(api, method, argument, response):
    api.endpoint("orgs.sites.listOrgSites", [])
    api.session.mist_get.return_value = response
    result = getattr(api.connection, method)(argument)
    assert (
        result["success"] is True
        and result["sites"] == []
        and result["total_sites"] == 0
    )
    assert api.session.mist_get.call_count == (1 if response is None else 3)
    assert api.sleep.call_count == (0 if response is None else 2)
