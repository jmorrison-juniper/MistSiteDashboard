#!/usr/bin/env python3
"""
Mist API Connection Module for MistSiteDashboard
Handles authentication and API calls to Juniper Mist Cloud.

Author: Joseph Morrison <jmorrison@juniper.net>
"""

import os
import logging
from typing import Dict, List, Optional, Any

import mistapi

logger = logging.getLogger(__name__)


class MistConnection:
    """Manages connection to the Juniper Mist Cloud API."""
    
    def __init__(self):
        """Initialize Mist API connection with credentials from environment."""
        self.api_token = os.getenv("MIST_APITOKEN")
        self.api_host = os.getenv("MIST_HOST", "api.mist.com")
        self.org_id = os.getenv("MIST_ORG_ID") or os.getenv("org_id")
        self.session = None
        
        if not self.api_token:
            logger.warning("MIST_APITOKEN not set in environment")
        
    def _get_session(self) -> mistapi.APISession:
        """Get or create an API session."""
        if self.session is None:
            self.session = mistapi.APISession(
                host=self.api_host,
                apitoken=self.api_token
            )
        return self.session
    
    def test_connection(self) -> Dict[str, Any]:
        """Test the API connection and return org info."""
        try:
            session = self._get_session()
            
            # Get org info to verify connection
            if self.org_id:
                response = mistapi.api.v1.orgs.orgs.getOrg(session, self.org_id)
                org_data = response.data if hasattr(response, "data") else response
                return {
                    "success": True,
                    "org_name": org_data.get("name", "Unknown"),
                    "org_id": self.org_id
                }
            else:
                # List orgs to find the first available
                response = mistapi.api.v1.self.self.getSelf(session)
                self_data = response.data if hasattr(response, "data") else response
                privileges = self_data.get("privileges", [])
                
                if privileges:
                    first_org = privileges[0]
                    self.org_id = first_org.get("org_id")
                    return {
                        "success": True,
                        "org_name": first_org.get("name", "Unknown"),
                        "org_id": self.org_id
                    }
                else:
                    return {"success": False, "error": "No organizations found for this API token"}
                    
        except Exception as error:
            logger.error(f"Connection test failed: {error}")
            return {"success": False, "error": str(error)}
    
    def get_sites(self) -> List[Dict[str, Any]]:
        """Get all sites in the organization."""
        try:
            session = self._get_session()
            
            if not self.org_id:
                test_result = self.test_connection()
                if not test_result["success"]:
                    raise ValueError("Could not determine organization ID")
            
            response = mistapi.api.v1.orgs.sites.listOrgSites(
                session, 
                self.org_id, 
                limit=1000
            )
            sites = mistapi.get_all(response=response, mist_session=session) or []
            
            # Sort sites by name
            sites.sort(key=lambda site: site.get("name", "").lower())
            
            # Return simplified site list for dropdown
            return [
                {
                    "id": site.get("id"),
                    "name": site.get("name", "Unknown"),
                    "address": site.get("address", ""),
                    "country_code": site.get("country_code", ""),
                    "timezone": site.get("timezone", "")
                }
                for site in sites
            ]
            
        except Exception as error:
            logger.error(f"Error fetching sites: {error}")
            raise
    
    def get_site_health(self, site_id: str) -> Dict[str, Any]:
        """Get device health statistics for a site."""
        try:
            session = self._get_session()
            
            # Get device stats for all device types
            response = mistapi.api.v1.sites.stats.listSiteDevicesStats(
                session, 
                site_id, 
                type="all", 
                limit=1000
            )
            devices = mistapi.get_all(response=response, mist_session=session) or []
            
            # Categorize devices and calculate health
            health_data = {
                "aps": {"total": 0, "connected": 0, "disconnected": 0, "devices": []},
                "switches": {"total": 0, "connected": 0, "disconnected": 0, "devices": []},
                "gateways": {"total": 0, "connected": 0, "disconnected": 0, "devices": []},
                "summary": {"total": 0, "connected": 0, "disconnected": 0, "health_percentage": 0}
            }
            
            for device in devices:
                device_type = device.get("type", "unknown")
                status = device.get("status", "unknown")
                is_connected = status == "connected"
                
                # Base device summary
                device_summary = {
                    "id": device.get("id"),
                    "name": device.get("name", "Unknown"),
                    "mac": device.get("mac", ""),
                    "model": device.get("model", ""),
                    "status": status,
                    "ip": device.get("ip", ""),
                    "version": device.get("version", ""),
                    "uptime": device.get("uptime", 0),
                    "last_seen": device.get("last_seen", 0),
                    "serial": device.get("serial", ""),
                    "notes": device.get("notes", "")
                }
                
                # Categorize by device type and add type-specific fields
                if device_type == "ap":
                    # AP-specific fields
                    device_summary["num_clients"] = device.get("num_clients", 0) or 0
                    device_summary["power_src"] = device.get("power_src", "")
                    device_summary["power_opmode"] = device.get("power_opmode", "")
                    # Get port speed from port_stat
                    port_stat = device.get("port_stat", {})
                    eth0_stat = port_stat.get("eth0", {})
                    device_summary["port_speed"] = eth0_stat.get("speed", 0)
                    
                    health_data["aps"]["total"] += 1
                    health_data["aps"]["connected" if is_connected else "disconnected"] += 1
                    health_data["aps"]["devices"].append(device_summary)
                elif device_type == "switch":
                    # Switch-specific fields
                    clients_stats = device.get("clients_stats", {}).get("total", {})
                    device_summary["num_wired_clients"] = clients_stats.get("num_wired_clients", 0) or 0
                    # num_aps comes as a list, get the first value
                    num_aps = clients_stats.get("num_aps", [0])
                    device_summary["num_aps"] = num_aps[0] if isinstance(num_aps, list) and num_aps else 0
                    
                    health_data["switches"]["total"] += 1
                    health_data["switches"]["connected" if is_connected else "disconnected"] += 1
                    health_data["switches"]["devices"].append(device_summary)
                elif device_type == "gateway":
                    health_data["gateways"]["total"] += 1
                    health_data["gateways"]["connected" if is_connected else "disconnected"] += 1
                    health_data["gateways"]["devices"].append(device_summary)
                
                # Update summary
                health_data["summary"]["total"] += 1
                health_data["summary"]["connected" if is_connected else "disconnected"] += 1
            
            # Calculate overall health percentage
            if health_data["summary"]["total"] > 0:
                health_data["summary"]["health_percentage"] = round(
                    (health_data["summary"]["connected"] / health_data["summary"]["total"]) * 100, 1
                )
            
            return health_data
            
        except Exception as error:
            logger.error(f"Error fetching site health for {site_id}: {error}")
            raise
    
    def get_site_sle(self, site_id: str) -> Dict[str, Any]:
        """Get SLE (Service Level Experience) metrics for a site with subcategories."""
        try:
            session = self._get_session()
            
            sle_data = {
                "wifi": {"metrics": {}, "available": False},
                "wired": {"metrics": {}, "available": False},
                "wan": {"metrics": {}, "available": False}
            }
            
            # Define which metrics belong to which category
            metric_categories = {
                "wifi": ["coverage", "capacity", "time-to-connect", "roaming", "throughput", "ap-availability", "ap-health"],
                "wired": ["switch-health", "switch-throughput", "switch-stc"],
                "wan": ["gateway-health", "wan-link-health", "application-health", "gateway-bandwidth"]
            }
            
            # Get list of enabled metrics for the site
            try:
                metrics_response = mistapi.api.v1.sites.sle.listSiteSlesMetrics(
                    session,
                    site_id,
                    scope="site",
                    scope_id=site_id
                )
                metrics_data = metrics_response.data if hasattr(metrics_response, "data") else metrics_response
                enabled_metrics = metrics_data.get("enabled", []) if metrics_data else []
            except Exception as e:
                logger.debug(f"Could not get enabled metrics for site {site_id}: {e}")
                enabled_metrics = []
            
            # Fetch summary for each enabled metric and categorize
            for metric in enabled_metrics:
                # Determine which category this metric belongs to
                category = None
                for cat, cat_metrics in metric_categories.items():
                    if metric in cat_metrics or any(metric.startswith(m) for m in cat_metrics):
                        category = cat
                        break
                
                if not category:
                    continue
                    
                try:
                    summary_response = mistapi.api.v1.sites.sle.getSiteSleSummary(
                        session,
                        site_id,
                        scope="site",
                        scope_id=site_id,
                        metric=metric,
                        duration="1d"
                    )
                    summary_data = summary_response.data if hasattr(summary_response, "data") else summary_response
                    
                    if summary_data and "sle" in summary_data:
                        # Calculate SLE percentage from samples
                        sle_info = summary_data.get("sle", {})
                        samples = sle_info.get("samples", {})
                        total_samples = samples.get("total", [])
                        degraded_samples = samples.get("degraded", [])
                        
                        # Sum up totals and degraded values (skip None values)
                        total_sum = sum([x for x in total_samples if x is not None])
                        degraded_sum = sum([x for x in degraded_samples if x is not None])
                        
                        if total_sum > 0:
                            sle_value = ((total_sum - degraded_sum) / total_sum) * 100
                            sle_data[category]["available"] = True
                            # Use a cleaner metric name for display
                            display_name = metric.replace("-v2", "").replace("-v4", "").replace("-new", "")
                            # Avoid duplicates (e.g., switch-health and switch-health-v2)
                            if display_name not in sle_data[category]["metrics"]:
                                sle_data[category]["metrics"][display_name] = round(sle_value, 1)
                            
                except Exception as metric_error:
                    logger.debug(f"Could not fetch {metric} for site {site_id}: {metric_error}")
            
            return sle_data
            
        except Exception as error:
            logger.error(f"Error fetching site SLE for {site_id}: {error}")
            raise
    
    def get_site_devices(self, site_id: str, device_type: str = "all") -> List[Dict[str, Any]]:
        """Get detailed device information for a site."""
        try:
            session = self._get_session()
            
            response = mistapi.api.v1.sites.stats.listSiteDevicesStats(
                session, 
                site_id, 
                type=device_type, 
                limit=1000
            )
            devices = mistapi.get_all(response=response, mist_session=session) or []
            
            return [
                {
                    "id": device.get("id"),
                    "name": device.get("name", "Unknown"),
                    "type": device.get("type", "unknown"),
                    "mac": device.get("mac", ""),
                    "model": device.get("model", ""),
                    "status": device.get("status", "unknown"),
                    "ip": device.get("ip", ""),
                    "version": device.get("version", ""),
                    "uptime": device.get("uptime", 0),
                    "last_seen": device.get("last_seen", 0),
                    "cpu_util": device.get("cpu_util", 0),
                    "mem_total_kb": device.get("mem_total_kb", 0),
                    "mem_used_kb": device.get("mem_used_kb", 0)
                }
                for device in devices
            ]
            
        except Exception as error:
            logger.error(f"Error fetching devices for site {site_id}: {error}")
            raise
