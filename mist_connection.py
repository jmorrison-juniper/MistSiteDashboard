#!/usr/bin/env python3
"""
Mist API Connection Module for MistSiteDashboard
Handles authentication and API calls to Juniper Mist Cloud.

Author: Joseph Morrison <jmorrison@juniper.net>
"""

import os
import time
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
                apitoken=self.api_token or ""
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
                if isinstance(org_data, dict):
                    return {
                        "success": True,
                        "org_name": org_data.get("name", "Unknown"),
                        "org_id": self.org_id
                    }
                else:
                    return {"success": True, "org_name": "Unknown", "org_id": self.org_id}
            else:
                # List orgs to find the first available
                response = mistapi.api.v1.self.self.getSelf(session)
                self_data = response.data if hasattr(response, "data") else response
                privileges = self_data.get("privileges", []) if isinstance(self_data, dict) else []
                
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
            
            # org_id is guaranteed to be set after test_connection succeeds
            org_id: str = self.org_id or ""
            
            response = mistapi.api.v1.orgs.sites.listOrgSites(
                session, 
                org_id, 
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
            org_id: str = self.org_id or ""
            
            # Build device profile -> SSIDs mapping
            # Chain: AP.deviceprofile_id -> Template.deviceprofile_ids -> WLAN.template_id
            deviceprofile_ssids: Dict[str, List[str]] = {}
            try:
                # Fetch org templates
                templates_response = mistapi.api.v1.orgs.templates.listOrgTemplates(
                    session, org_id
                )
                templates_data = templates_response.data if hasattr(templates_response, "data") else templates_response
                templates = templates_data if isinstance(templates_data, list) else []
                
                # Build template_id -> deviceprofile_ids mapping
                template_to_deviceprofiles: Dict[str, List[str]] = {}
                for template in templates:
                    if isinstance(template, dict):
                        template_id = template.get("id", "")
                        dp_ids = template.get("deviceprofile_ids", [])
                        filter_by_dp = template.get("filter_by_deviceprofile", False)
                        if template_id and dp_ids and filter_by_dp:
                            template_to_deviceprofiles[template_id] = dp_ids
                
                # Fetch org WLANs
                wlans_response = mistapi.api.v1.orgs.wlans.listOrgWlans(
                    session, org_id
                )
                wlans_data = wlans_response.data if hasattr(wlans_response, "data") else wlans_response
                org_wlans = wlans_data if isinstance(wlans_data, list) else []
                
                # Build deviceprofile_id -> SSIDs mapping
                for wlan in org_wlans:
                    if not isinstance(wlan, dict):
                        continue
                    if not wlan.get("enabled", True):
                        continue
                    ssid = wlan.get("ssid", "")
                    template_id = wlan.get("template_id", "")
                    if not ssid:
                        continue
                    
                    # If WLAN has a template_id, map it to device profiles via template
                    if template_id and template_id in template_to_deviceprofiles:
                        for dp_id in template_to_deviceprofiles[template_id]:
                            if dp_id not in deviceprofile_ssids:
                                deviceprofile_ssids[dp_id] = []
                            if ssid not in deviceprofile_ssids[dp_id]:
                                deviceprofile_ssids[dp_id].append(ssid)
                
                logger.debug(f"Built SSID mapping for {len(deviceprofile_ssids)} device profiles")
            except Exception as mapping_error:
                logger.warning(f"Could not build device profile SSID mapping: {mapping_error}")
            
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
                    
                    # Get SSIDs for this AP based on its device profile
                    # Uses the deviceprofile_id -> SSIDs mapping built from templates
                    ap_deviceprofile_id = device.get("deviceprofile_id", "")
                    ap_ssids: List[str] = deviceprofile_ssids.get(ap_deviceprofile_id, [])
                    device_summary["ssids"] = ap_ssids.copy() if ap_ssids else []
                    
                    health_data["aps"]["total"] += 1
                    health_data["aps"]["connected" if is_connected else "disconnected"] += 1
                    health_data["aps"]["devices"].append(device_summary)
                elif device_type == "switch":
                    # Switch-specific fields
                    clients_stats = device.get("clients_stats", {}).get("total", {})
                    device_summary["num_wired_clients"] = clients_stats.get("num_wired_clients", 0) or 0
                    device_summary["num_wifi_clients"] = clients_stats.get("num_wifi_clients", 0) or 0
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
    
    def get_site_sle(self, site_id: str, duration: str = "1d") -> Dict[str, Any]:
        """Get SLE (Service Level Experience) metrics for a site with subcategories.
        
        Args:
            site_id: The site ID to get SLE metrics for
            duration: Time range - '10m', '1h', 'today', '1d', '1w' (default: '1d')
        """
        try:
            session = self._get_session()
            
            # Determine if we need to use start/end timestamps or duration parameter
            # The API supports duration values like '1h', '1d', '1w' but for 10 minutes
            # we need to use explicit start/end epoch timestamps
            use_timestamps = duration == '10m'
            
            start_time: int = 0
            end_time: int = 0
            api_duration: str = '1d'
            
            if use_timestamps:
                # Calculate start/end epoch timestamps for 10 minutes
                end_time = int(time.time())
                start_time = end_time - 600  # 10 minutes = 600 seconds
            else:
                # Map duration values to API-compatible formats
                duration_map = {
                    '1h': '1h',
                    'today': '1d',
                    '1d': '1d',
                    '1w': '1w'
                }
                api_duration = duration_map.get(duration, '1d')
            
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
                enabled_metrics = metrics_data.get("enabled", []) if isinstance(metrics_data, dict) else []
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
                    # Use start/end timestamps for 10m, otherwise use duration parameter
                    if use_timestamps:
                        summary_response = mistapi.api.v1.sites.sle.getSiteSleSummary(
                            session,
                            site_id,
                            scope="site",
                            scope_id=site_id,
                            metric=metric,
                            start=start_time,
                            end=end_time
                        )
                    else:
                        summary_response = mistapi.api.v1.sites.sle.getSiteSleSummary(
                            session,
                            site_id,
                            scope="site",
                            scope_id=site_id,
                            metric=metric,
                            duration=api_duration
                        )
                    summary_data = summary_response.data if hasattr(summary_response, "data") else summary_response
                    
                    if isinstance(summary_data, dict) and "sle" in summary_data:
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
    
    def get_sle_classifiers(
        self, site_id: str, metric: str, scope: str = "site"
    ) -> List[Dict[str, Any]]:
        """Get list of classifiers for a specific SLE metric.
        
        Args:
            site_id: The site ID
            metric: The SLE metric name (e.g., 'coverage', 'capacity', 'time-to-connect')
            scope: The scope type ('site', 'ap', 'switch', 'gateway', 'client')
        
        Returns:
            List of classifier dictionaries with name and impact info
        """
        try:
            session = self._get_session()
            
            response = mistapi.api.v1.sites.sle.listSiteSleMetricClassifiers(
                session,
                site_id,
                scope=scope,
                scope_id=site_id,
                metric=metric
            )
            data = response.data if hasattr(response, "data") else response
            
            if isinstance(data, dict):
                classifiers = data.get("classifiers", [])
                return classifiers if isinstance(classifiers, list) else []
            return []
            
        except Exception as error:
            logger.error(f"Error fetching SLE classifiers for {metric}: {error}")
            return []
    
    def get_sle_classifier_details(
        self,
        site_id: str,
        metric: str,
        classifier: str,
        duration: str = "1d",
        scope: str = "site"
    ) -> Dict[str, Any]:
        """Get detailed breakdown for a specific SLE classifier.
        
        Args:
            site_id: The site ID
            metric: The SLE metric name
            classifier: The classifier name
            duration: Time range ('1h', '1d', '1w')
            scope: The scope type
        
        Returns:
            Dictionary with classifier details and samples
        """
        try:
            session = self._get_session()
            
            response = mistapi.api.v1.sites.sle.getSiteSleClassifierDetails(
                session,
                site_id,
                scope=scope,
                scope_id=site_id,
                metric=metric,
                classifier=classifier,
                duration=duration
            )
            data = response.data if hasattr(response, "data") else response
            
            return data if isinstance(data, dict) else {}
            
        except Exception as error:
            logger.error(f"Error fetching classifier details for {metric}/{classifier}: {error}")
            return {}
    
    def get_sle_impact_summary(
        self,
        site_id: str,
        metric: str,
        duration: str = "1d",
        classifier: Optional[str] = None,
        scope: str = "site"
    ) -> Dict[str, Any]:
        """Get impact summary showing affected clients/devices for an SLE metric.
        
        Args:
            site_id: The site ID
            metric: The SLE metric name
            duration: Time range ('1h', '1d', '1w')
            classifier: Optional classifier to filter by
            scope: The scope type
        
        Returns:
            Dictionary with impact summary data
        """
        try:
            session = self._get_session()
            
            kwargs: Dict[str, Any] = {
                "mist_session": session,
                "site_id": site_id,
                "scope": scope,
                "scope_id": site_id,
                "metric": metric,
                "duration": duration
            }
            if classifier:
                kwargs["classifier"] = classifier
            
            response = mistapi.api.v1.sites.sle.getSiteSleImpactSummary(**kwargs)
            data = response.data if hasattr(response, "data") else response
            
            return data if isinstance(data, dict) else {}
            
        except Exception as error:
            logger.error(f"Error fetching SLE impact summary for {metric}: {error}")
            return {}
    
    def get_sle_details(self, site_id: str, category: str, duration: str = "1d") -> Dict[str, Any]:
        """Get comprehensive SLE details for a category (wifi, wired, or wan).
        
        This method fetches all metrics for the category with their classifiers
        and impact data for the detail pages.
        
        Args:
            site_id: The site ID
            category: SLE category ('wifi', 'wired', 'wan')
            duration: Time range ('1h', '1d', '1w')
        
        Returns:
            Dictionary with metrics, classifiers, and impact data
        """
        try:
            session = self._get_session()
            
            # Define which metrics belong to which category
            metric_categories = {
                "wifi": ["coverage", "capacity", "time-to-connect", "roaming", "throughput", "ap-availability", "ap-health"],
                "wired": ["switch-health", "switch-throughput", "switch-stc"],
                "wan": ["gateway-health", "wan-link-health", "application-health", "gateway-bandwidth"]
            }
            
            if category not in metric_categories:
                raise ValueError(f"Invalid category: {category}")
            
            # Get enabled metrics for site first
            try:
                metrics_response = mistapi.api.v1.sites.sle.listSiteSlesMetrics(
                    session,
                    site_id,
                    scope="site",
                    scope_id=site_id
                )
                metrics_data = metrics_response.data if hasattr(metrics_response, "data") else metrics_response
                enabled_metrics = metrics_data.get("enabled", []) if isinstance(metrics_data, dict) else []
            except Exception as e:
                logger.debug(f"Could not get enabled metrics: {e}")
                enabled_metrics = []
            
            # Filter to only metrics in this category that are enabled
            category_metrics = [
                m for m in metric_categories[category]
                if m in enabled_metrics or any(
                    em.startswith(m) for em in enabled_metrics
                )
            ]
            
            result: Dict[str, Any] = {
                "category": category,
                "duration": duration,
                "metrics": {}
            }
            
            for metric in category_metrics:
                # Find actual metric name (might have version suffix like -v2)
                actual_metric = metric
                for em in enabled_metrics:
                    if em.startswith(metric):
                        actual_metric = em
                        break
                
                metric_data: Dict[str, Any] = {
                    "name": metric,
                    "sle_value": None,
                    "classifiers": [],
                    "impact": {}
                }
                
                # Get SLE summary value
                try:
                    summary_response = mistapi.api.v1.sites.sle.getSiteSleSummary(
                        session,
                        site_id,
                        scope="site",
                        scope_id=site_id,
                        metric=actual_metric,
                        duration=duration
                    )
                    summary_data = summary_response.data if hasattr(summary_response, "data") else summary_response
                    
                    if isinstance(summary_data, dict) and "sle" in summary_data:
                        sle_info = summary_data.get("sle", {})
                        samples = sle_info.get("samples", {})
                        total = sum(x for x in samples.get("total", []) if x is not None)
                        degraded = sum(x for x in samples.get("degraded", []) if x is not None)
                        
                        if total > 0:
                            metric_data["sle_value"] = round(((total - degraded) / total) * 100, 1)
                except Exception as e:
                    logger.debug(f"Could not get summary for {actual_metric}: {e}")
                
                # Get classifiers for this metric
                try:
                    classifiers_response = mistapi.api.v1.sites.sle.listSiteSleMetricClassifiers(
                        session,
                        site_id,
                        scope="site",
                        scope_id=site_id,
                        metric=actual_metric
                    )
                    classifiers_data = classifiers_response.data if hasattr(classifiers_response, "data") else classifiers_response
                    raw_classifiers = classifiers_data.get("classifiers", []) if isinstance(classifiers_data, dict) else []
                    
                    # Get details for each classifier
                    for clf in raw_classifiers:
                        clf_name = clf if isinstance(clf, str) else clf.get("name", str(clf))
                        
                        try:
                            clf_details = mistapi.api.v1.sites.sle.getSiteSleClassifierDetails(
                                session,
                                site_id,
                                scope="site",
                                scope_id=site_id,
                                metric=actual_metric,
                                classifier=clf_name,
                                duration=duration
                            )
                            clf_data = clf_details.data if hasattr(clf_details, "data") else clf_details
                            
                            classifier_info = {
                                "name": clf_name,
                                "impact": clf_data.get("impact", {}) if isinstance(clf_data, dict) else {},
                                "samples": clf_data.get("samples", {}) if isinstance(clf_data, dict) else {}
                            }
                            metric_data["classifiers"].append(classifier_info)
                        except Exception as clf_error:
                            logger.debug(f"Could not get classifier details for {clf_name}: {clf_error}")
                            metric_data["classifiers"].append({"name": clf_name, "impact": {}, "samples": {}})
                    
                except Exception as e:
                    logger.debug(f"Could not get classifiers for {actual_metric}: {e}")
                
                # Get impact summary
                try:
                    impact_response = mistapi.api.v1.sites.sle.getSiteSleImpactSummary(
                        session,
                        site_id,
                        scope="site",
                        scope_id=site_id,
                        metric=actual_metric,
                        duration=duration
                    )
                    impact_data = impact_response.data if hasattr(impact_response, "data") else impact_response
                    metric_data["impact"] = impact_data if isinstance(impact_data, dict) else {}
                except Exception as e:
                    logger.debug(f"Could not get impact for {actual_metric}: {e}")
                
                result["metrics"][metric] = metric_data
            
            return result
            
        except Exception as error:
            logger.error(f"Error fetching SLE details for {category}: {error}")
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

    def get_wireless_client_sessions(self, site_id: str) -> List[Dict[str, Any]]:
        """Get wireless client data combining sessions, client stats, and client search.
        
        This provides the most complete view of wireless clients by merging data from:
        - searchSiteWirelessClientSessions (historical sessions)
        - listSiteWirelessClientsStats (current connected clients with detailed stats)
        - searchSiteWirelessClients (client search with hostname, username, etc.)
        """
        try:
            session = self._get_session()
            clients_by_mac = {}
            
            # 1. Get current connected clients with stats (real-time data)
            try:
                stats_response = mistapi.api.v1.sites.stats.listSiteWirelessClientsStats(
                    session, 
                    site_id
                )
                stats_results = mistapi.get_all(response=stats_response, mist_session=session) or []
                
                for client in stats_results:
                    mac = client.get("mac", "")
                    if mac:
                        clients_by_mac[mac] = {
                            "mac": mac,
                            "hostname": client.get("hostname", ""),
                            "ip": client.get("ip", ""),
                            "username": client.get("username", ""),
                            "ssid": client.get("ssid", ""),
                            "ap": client.get("ap_mac", ""),
                            "band": client.get("band", ""),
                            "os": client.get("os", ""),
                            "manufacture": client.get("manufacture", ""),
                            "last_seen": client.get("last_seen", 0),
                            "assoc_time": client.get("assoc_time", 0),
                            "uptime": client.get("uptime", 0),
                            "rssi": client.get("rssi", 0),
                            "is_connected": True
                        }
            except Exception as e:
                logger.debug(f"Could not fetch wireless client stats: {e}")
            
            # 2. Get client search data (has hostname, username, last values)
            try:
                search_response = mistapi.api.v1.sites.clients.searchSiteWirelessClients(
                    session, 
                    site_id,
                    limit=1000
                )
                search_results = mistapi.get_all(response=search_response, mist_session=session) or []
                
                for client in search_results:
                    mac = client.get("mac", "")
                    if mac:
                        if mac in clients_by_mac:
                            # Merge with existing data, prefer non-empty values
                            existing = clients_by_mac[mac]
                            existing["hostname"] = existing.get("hostname") or client.get("last_hostname", "")
                            existing["ip"] = existing.get("ip") or client.get("last_ip", "")
                            existing["username"] = existing.get("username") or client.get("last_username", "")
                            existing["os"] = existing.get("os") or client.get("last_os", "")
                            existing["ssid"] = existing.get("ssid") or client.get("last_ssid", "")
                        else:
                            clients_by_mac[mac] = {
                                "mac": mac,
                                "hostname": client.get("last_hostname", ""),
                                "ip": client.get("last_ip", ""),
                                "username": client.get("last_username", ""),
                                "ssid": client.get("last_ssid", ""),
                                "ap": client.get("last_ap", ""),
                                "band": client.get("band", ""),
                                "os": client.get("last_os", ""),
                                "manufacture": client.get("mfg", ""),
                                "last_seen": client.get("timestamp", 0),
                                "assoc_time": 0,
                                "uptime": 0,
                                "rssi": 0,
                                "is_connected": False
                            }
            except Exception as e:
                logger.debug(f"Could not fetch wireless client search: {e}")
            
            # 3. Get session history for the last 7 days (for disconnect times and historical sessions)
            try:
                sessions_response = mistapi.api.v1.sites.clients.searchSiteWirelessClientSessions(
                    session, 
                    site_id,
                    duration="7d",
                    limit=1000
                )
                sessions_results = mistapi.get_all(response=sessions_response, mist_session=session) or []
                
                for sess in sessions_results:
                    mac = sess.get("mac", "")
                    if mac:
                        if mac in clients_by_mac:
                            # Update with session data if more recent or has more info
                            existing = clients_by_mac[mac]
                            disconnect = sess.get("disconnect", 0)
                            if disconnect > existing.get("last_seen", 0):
                                existing["last_seen"] = disconnect
                            existing["connect"] = existing.get("connect") or sess.get("connect", 0)
                            existing["disconnect"] = sess.get("disconnect", 0)
                            existing["duration"] = sess.get("duration", 0)
                            existing["ssid"] = existing.get("ssid") or sess.get("ssid", "")
                            existing["manufacture"] = existing.get("manufacture") or sess.get("client_manufacture", "")
                        else:
                            clients_by_mac[mac] = {
                                "mac": mac,
                                "hostname": "",
                                "ip": "",
                                "username": "",
                                "ssid": sess.get("ssid", ""),
                                "ap": sess.get("ap", ""),
                                "band": sess.get("band", ""),
                                "os": "",
                                "manufacture": sess.get("client_manufacture", ""),
                                "last_seen": sess.get("disconnect", 0),
                                "connect": sess.get("connect", 0),
                                "disconnect": sess.get("disconnect", 0),
                                "duration": sess.get("duration", 0),
                                "assoc_time": 0,
                                "uptime": 0,
                                "rssi": 0,
                                "is_connected": False
                            }
            except Exception as e:
                logger.debug(f"Could not fetch wireless client sessions: {e}")
            
            return list(clients_by_mac.values())
            
        except Exception as error:
            logger.error(f"Error fetching wireless clients for site {site_id}: {error}")
            raise

    def get_wired_clients(self, site_id: str) -> List[Dict[str, Any]]:
        """Get wired client information combining search and stats.
        
        Uses searchSiteWiredClients for client data including DHCP info.
        """
        try:
            session = self._get_session()
            clients_by_mac = {}
            current_time = int(time.time())
            
            # Get wired clients from search API
            try:
                response = mistapi.api.v1.sites.wired_clients.searchSiteWiredClients(
                    session, 
                    site_id,
                    duration="7d",
                    limit=1000
                )
                all_results = mistapi.get_all(response=response, mist_session=session) or []
                
                for client in all_results:
                    mac = client.get("mac", "")
                    if mac:
                        # Get IP from device_mac_port if available
                        device_mac_port = client.get("device_mac_port", [])
                        ip_list = client.get("ip", [])
                        port_info = {}
                        
                        if device_mac_port and isinstance(device_mac_port, list) and len(device_mac_port) > 0:
                            port_info = device_mac_port[0] if isinstance(device_mac_port[0], dict) else {}
                        
                        # Get the best IP available
                        ip = ""
                        if isinstance(ip_list, list) and ip_list:
                            ip = ip_list[0]
                        elif isinstance(ip_list, str):
                            ip = ip_list
                        elif port_info.get("ip"):
                            ip = port_info.get("ip", "")
                        
                        # Get timestamps for connected time and last seen
                        timestamp = client.get("timestamp", 0)
                        port_start = port_info.get("start", 0)
                        
                        # Ensure timestamps are valid integers
                        if isinstance(timestamp, str):
                            try:
                                timestamp = int(float(timestamp)) if timestamp else 0
                            except (ValueError, TypeError):
                                timestamp = 0
                        if isinstance(port_start, str):
                            try:
                                port_start = int(float(port_start)) if port_start else 0
                            except (ValueError, TypeError):
                                port_start = 0
                        
                        # Determine connection status - if seen within last 5 minutes, consider connected
                        last_seen = timestamp if timestamp else port_start
                        is_connected = (current_time - last_seen) < 300 if last_seen else False
                        
                        # Only use connected_time if it's a valid positive timestamp
                        connected_time = port_start if port_start > 0 else (timestamp if timestamp > 0 else 0)
                        
                        clients_by_mac[mac] = {
                            "mac": mac,
                            "hostname": client.get("dhcp_hostname", "") or client.get("dhcp_fqdn", ""),
                            "ip": ip,
                            "username": client.get("username", ""),
                            "connected_time": connected_time,
                            "last_seen": last_seen,
                            "device_type": client.get("dhcp_vendor_class_identifier", "") or client.get("dhcp_fingerprint", ""),
                            "is_connected": is_connected,
                            "switch_mac": port_info.get("device_mac", "") or (client.get("device_mac", [""])[0] if client.get("device_mac") else ""),
                            "port_id": port_info.get("port_id", "")
                        }
            except Exception as e:
                logger.debug(f"Could not fetch wired clients: {e}")
            
            return list(clients_by_mac.values())
            
        except Exception as error:
            logger.error(f"Error fetching wired clients for site {site_id}: {error}")
            raise

    def get_gateway_wan_status(self, site_id: str) -> List[Dict[str, Any]]:
        """Get gateway device stats including WAN port information, VPN peers, and BGP peers."""
        try:
            session = self._get_session()
            
            if not self.org_id:
                test_result = self.test_connection()
                if not test_result["success"]:
                    raise ValueError("Could not determine organization ID")
            
            response = mistapi.api.v1.sites.stats.listSiteDevicesStats(
                session, 
                site_id, 
                type="gateway", 
                limit=100
            )
            gateways = mistapi.get_all(response=response, mist_session=session) or []
            
            # Get org_id for VPN/BGP queries (guaranteed set after test_connection)
            org_id: str = self.org_id or ""
            
            gateway_list = []
            for gw in gateways:
                gw_mac = gw.get("mac", "")
                
                gw_info = {
                    "id": gw.get("id"),
                    "name": gw.get("name", "Unknown"),
                    "mac": gw_mac,
                    "model": gw.get("model", ""),
                    "status": gw.get("status", "unknown"),
                    "serial": gw.get("serial", ""),
                    "version": gw.get("version", ""),
                    "uptime": gw.get("uptime", 0),
                    "ext_ip": gw.get("ext_ip", ""),
                    "wan_ports": [],
                    "vpn_peers": [],
                    "bgp_peers": []
                }
                
                # Extract WAN port information from if_stat
                # Only include ports where port_usage == "wan" or wan_type is set
                if_stat = gw.get("if_stat", {})
                
                for port_name, port_stats in if_stat.items():
                    if isinstance(port_stats, dict):
                        port_usage = port_stats.get("port_usage", "")
                        wan_type = port_stats.get("wan_type", "")
                        
                        # Only include WAN ports
                        if port_usage == "wan" or wan_type:
                            # Get IP from ips array
                            ips = port_stats.get("ips", [])
                            ip_str = ips[0] if isinstance(ips, list) and ips else ""
                            
                            wan_port = {
                                "name": port_name,
                                "wan_name": port_stats.get("wan_name", port_name),
                                "status": "up" if port_stats.get("up", False) else "down",
                                "ip": ip_str,
                                "wan_type": wan_type or "ethernet",
                                "address_mode": port_stats.get("address_mode", ""),
                                "vlan": port_stats.get("vlan", 0),
                                "port_id": port_stats.get("port_id", ""),
                                "rx_bytes": port_stats.get("rx_bytes", 0),
                                "tx_bytes": port_stats.get("tx_bytes", 0),
                                "rx_pkts": port_stats.get("rx_pkts", 0),
                                "tx_pkts": port_stats.get("tx_pkts", 0)
                            }
                            gw_info["wan_ports"].append(wan_port)
                
                # Fetch VPN peers for this gateway using peer path stats
                try:
                    vpn_response = mistapi.api.v1.orgs.stats.searchOrgPeerPathStats(
                        session,
                        org_id,
                        site_id=site_id,
                        mac=gw_mac,
                        limit=100
                    )
                    if vpn_response and hasattr(vpn_response, 'data'):
                        vpn_data = vpn_response.data
                        vpn_results = vpn_data.get("results", []) if isinstance(vpn_data, dict) else []
                        for vpn in vpn_results:
                            vpn_peer = {
                                "vpn_name": vpn.get("vpn_name", ""),
                                "vpn_role": vpn.get("vpn_role", ""),
                                "type": vpn.get("type", ""),
                                "wan_name": vpn.get("wan_name", ""),
                                "peer_router_name": vpn.get("peer_router_name", ""),
                                "peer_mac": vpn.get("peer_mac", ""),
                                "up": vpn.get("up", False),
                                "is_active": vpn.get("is_active", False),
                                "uptime": vpn.get("uptime", 0),
                                "latency": vpn.get("latency", 0),
                                "jitter": vpn.get("jitter", 0),
                                "loss": vpn.get("loss", 0),
                                "mos": vpn.get("mos", 0),
                                "mtu": vpn.get("mtu", 0),
                                "hop_count": vpn.get("hop_count", 0)
                            }
                            gw_info["vpn_peers"].append(vpn_peer)
                except Exception as e:
                    logger.debug(f"Could not fetch VPN peers for gateway {gw_mac}: {e}")
                
                # Fetch BGP peers for this gateway
                try:
                    bgp_response = mistapi.api.v1.orgs.stats.searchOrgBgpStats(
                        session,
                        org_id,
                        site_id=site_id,
                        mac=gw_mac,
                        limit=100
                    )
                    if bgp_response and hasattr(bgp_response, 'data'):
                        bgp_data = bgp_response.data
                        bgp_results = bgp_data.get("results", []) if isinstance(bgp_data, dict) else []
                        for bgp in bgp_results:
                            bgp_peer = {
                                "neighbor": bgp.get("neighbor", ""),
                                "neighbor_mac": bgp.get("neighbor_mac", ""),
                                "vrf_name": bgp.get("vrf_name", ""),
                                "local_as": bgp.get("local_as", 0),
                                "neighbor_as": bgp.get("neighbor_as", 0),
                                "state": bgp.get("state", ""),
                                "up": bgp.get("up", False),
                                "uptime": bgp.get("uptime", 0),
                                "rx_pkts": bgp.get("rx_pkts", 0),
                                "tx_pkts": bgp.get("tx_pkts", 0),
                                "rx_routes": bgp.get("rx_routes", 0),
                                "tx_routes": bgp.get("tx_routes", 0),
                                "for_overlay": bgp.get("for_overlay", False)
                            }
                            gw_info["bgp_peers"].append(bgp_peer)
                except Exception as e:
                    logger.debug(f"Could not fetch BGP peers for gateway {gw_mac}: {e}")
                
                gateway_list.append(gw_info)
            
            return gateway_list
            
        except Exception as error:
            logger.error(f"Error fetching gateway WAN status for site {site_id}: {error}")
            raise
