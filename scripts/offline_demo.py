"""Serve real dashboard templates with synthetic data and no Mist connection."""

from __future__ import annotations

import argparse
from typing import Any
from unittest.mock import patch

from scripts.benchmark_routes import FakeMistConnection, dashboard_app


class OfflineMistConnection(FakeMistConnection):
    def get_org_worst_sites_by_metric(
        self, metric: str, duration: str = "1d", limit: int = 100
    ) -> dict[str, Any]:
        sites = [
            {
                "site_id": site["id"],
                "site_name": site["name"],
                metric: 0.94 + index / 100,
                "num_aps": 2,
                "num_switches": 1,
                "num_gateways": 1,
                "num_clients": 12,
            }
            for index, site in enumerate(self.get_sites())
        ]
        return {"success": True, "metric": metric, "sites": sites[:limit]}

    def get_site_health(self, site_id: str) -> dict[str, Any]:
        health = super().get_site_health(site_id)
        for group, device_type in (("switches", "switch"), ("gateways", "gateway")):
            health[group] = {
                "total": 1,
                "connected": 1,
                "disconnected": 0,
                "devices": [self._device(0, device_type)],
            }
        health["summary"].update(total=self.count + 2, connected=self.count + 2)
        return health

    def get_wireless_client_sessions(self, site_id: str) -> list[dict[str, Any]]:
        return [
            {
                "mac": f"02:00:00:00:00:{index:02x}",
                "hostname": f"demo-laptop-{index + 1}",
                "ip": f"192.0.2.{index + 10}",
                "ssid": "Demo Office",
                "band": "5",
                "rssi": -55,
                "is_connected": True,
                "connected_time": 1791158400,
                "last_seen": 1791162000,
                "device_type": "Synthetic workstation",
                "os": "Demo OS",
            }
            for index in range(self.count)
        ]

    def get_site_sle(self, site_id: str, duration: str = "1d") -> dict[str, Any]:
        return {
            "wifi": {
                "metrics": {"coverage": 99.0, "time-to-connect": 97.5},
                "available": True,
            },
            "wired": {"metrics": {"switch-health-v2": 99.0}, "available": True},
            "wan": {"metrics": {"gateway-health": 98.0}, "available": True},
        }


class OfflineDemo:
    @staticmethod
    def run() -> None:
        parser = argparse.ArgumentParser(description=__doc__)
        parser.add_argument("--port", type=int, default=5055)
        arguments = parser.parse_args()
        connection = OfflineMistConnection()
        with patch.object(
            dashboard_app, "get_mist_connection", return_value=connection
        ):
            dashboard_app.app.run(
                host="127.0.0.1", port=arguments.port, use_reloader=False
            )


if __name__ == "__main__":
    OfflineDemo.run()
