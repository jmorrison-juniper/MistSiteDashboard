"""Benchmark MistSiteDashboard API route latency with a fake MistConnection."""

from __future__ import annotations

import argparse
import logging
import statistics
import sys
import time
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import app as dashboard_app  # noqa: E402


class FakeMistConnection:
    def __init__(self, size: str = "small") -> None:
        counts = {"small": 2, "medium": 25, "large": 150}
        self.count = counts[size]

    def test_connection(self) -> dict[str, Any]:
        return {"success": True, "org_name": "Synthetic Org", "org_id": "org-1"}

    def get_sites(self) -> list[dict[str, Any]]:
        return [{"id": f"site-{i}", "name": f"Site {i}", "timezone": "UTC"} for i in range(self.count)]

    def get_org_sle_insights(self, sle_type: str, duration: str = "1d") -> dict[str, Any]:
        return {"success": True, "sle_type": sle_type, "duration": duration, "sites": self.get_sites()}

    def get_org_worst_sites_by_metric(self, metric: str, duration: str = "1d", limit: int = 100) -> dict[str, Any]:
        return {"success": True, "metric": metric, "duration": duration, "sites": self.get_sites()[:limit]}

    def get_site_health(self, site_id: str) -> dict[str, Any]:
        devices = [self._device(i, "ap") for i in range(self.count)]
        return {
            "aps": {"total": self.count, "connected": self.count, "disconnected": 0, "devices": devices},
            "switches": {"total": 0, "connected": 0, "disconnected": 0, "devices": []},
            "gateways": {"total": 0, "connected": 0, "disconnected": 0, "devices": []},
            "summary": {"total": self.count, "connected": self.count, "disconnected": 0, "health_percentage": 100.0},
        }

    def get_site_sle(self, site_id: str, duration: str = "1d") -> dict[str, Any]:
        return {
            "wifi": {"metrics": {"coverage": 99.0}, "available": True},
            "wired": {"metrics": {"switch-health-v2": 99.0}, "available": True},
            "wan": {"metrics": {"gateway-health": 99.0}, "available": True},
        }

    def get_site_devices(self, site_id: str, device_type: str = "all") -> list[dict[str, Any]]:
        return [self._device(i, "ap") for i in range(self.count)]

    def get_wireless_client_sessions(self, site_id: str) -> list[dict[str, Any]]:
        return [{"mac": f"00:00:00:00:00:{i:02x}", "hostname": f"client-{i}", "is_connected": True} for i in range(self.count)]

    def get_wired_clients(self, site_id: str) -> list[dict[str, Any]]:
        return self.get_wireless_client_sessions(site_id)

    def get_gateway_wan_status(self, site_id: str) -> list[dict[str, Any]]:
        return [{"id": f"gw-{i}", "name": f"Gateway {i}", "status": "connected", "wan_ports": []} for i in range(max(1, self.count // 10))]

    def get_sle_details(self, site_id: str, category: str, duration: str = "1d") -> dict[str, Any]:
        return {"category": category, "duration": duration, "metrics": {"coverage": {"sle_value": 99.0, "classifiers": []}}}

    def get_classifier_impact_details(self, site_id: str, metric: str, classifier: str, duration: str = "1d") -> dict[str, Any]:
        return {"metric": metric, "classifier": classifier, "aps": []}

    def get_sle_impacted_items(self, site_id: str, metric: str, item_type: str, duration: str = "1d", classifier: str | None = None) -> dict[str, Any]:
        return {"metric": metric, "item_type": item_type, "items": []}

    def get_site_info(self, site_id: str) -> dict[str, Any]:
        return {"id": site_id, "name": "Synthetic Site"}

    @staticmethod
    def _device(index: int, device_type: str) -> dict[str, Any]:
        return {"id": f"{device_type}-{index}", "name": f"{device_type.upper()} {index}", "type": device_type, "status": "connected"}


ROUTES = [
    ("GET", "/health"),
    ("GET", "/api/sites"),
    ("GET", "/api/org/sle/wifi"),
    ("GET", "/api/org/sle/wifi/metric/coverage"),
    ("GET", "/api/sites/site-1/health"),
    ("GET", "/api/sites/site-1/sle"),
    ("GET", "/api/sites/site-1/devices"),
    ("GET", "/api/sites/site-1/wireless-clients"),
    ("GET", "/api/sites/site-1/wired-clients"),
    ("GET", "/api/sites/site-1/gateway-wan"),
    ("GET", "/api/sites/site-1/sle/wifi"),
    ("GET", "/api/sites/site-1/sle/impact/coverage/client-usage"),
    ("GET", "/api/sites/site-1/sle/coverage/impacted/clients"),
    ("POST", "/api/test-connection"),
]


def percentile(values: list[float], pct: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, int(round((pct / 100) * (len(ordered) - 1))))
    return ordered[index]


def run_benchmark(iterations: int, size: str) -> list[dict[str, Any]]:
    dashboard_app._mist_connection = FakeMistConnection(size)
    dashboard_app.app.config["TESTING"] = True
    results: list[dict[str, Any]] = []
    with dashboard_app.app.test_client() as client:
        for method, route in ROUTES:
            durations: list[float] = []
            sizes: list[int] = []
            status = None
            for _ in range(iterations):
                start = time.perf_counter()
                response = client.open(route, method=method)
                durations.append((time.perf_counter() - start) * 1000)
                sizes.append(len(response.get_data()))
                status = response.status_code
            results.append(
                {
                    "method": method,
                    "route": route,
                    "status": status,
                    "p50_ms": statistics.median(durations),
                    "p95_ms": percentile(durations, 95),
                    "bytes": statistics.median(sizes),
                }
            )
    return results


def main() -> int:
    logging.getLogger("msd.perf").disabled = True
    logging.getLogger("app").setLevel(logging.WARNING)

    parser = argparse.ArgumentParser(description="Benchmark MistSiteDashboard routes with synthetic data.")
    parser.add_argument("--iterations", "-n", type=int, default=10, help="Requests per route.")
    parser.add_argument("--size", choices=("small", "medium", "large"), default="small", help="Synthetic payload size.")
    args = parser.parse_args()

    print(f"Benchmark size={args.size} iterations={args.iterations}")
    print("method route status p50_ms p95_ms bytes")
    for row in run_benchmark(args.iterations, args.size):
        print(
            f"{row['method']} {row['route']} {row['status']} "
            f"{row['p50_ms']:.3f} {row['p95_ms']:.3f} {int(row['bytes'])}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
