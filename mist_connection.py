#!/usr/bin/env python3
"""
Mist API Connection Module for MistSiteDashboard

This module handles all authentication and API interactions with the Juniper Mist
Cloud platform. It provides a high-level interface for retrieving site data, device
statistics, SLE metrics, and client information.

Architecture Overview:
    - MistConnection class encapsulates all API interactions
    - Uses the mistapi SDK (by Thomas Munzer) for low-level API calls
    - Implements lazy session initialization for performance
    - Handles pagination automatically using mistapi.get_all()
    - Provides data normalization and flattening for frontend consumption

Key Features:
    - Automatic organization ID detection from API token
    - Session reuse for improved performance
    - Device profile to SSID resolution via template chain
    - Comprehensive SLE metrics with classifier analysis
    - Client data aggregation from multiple API sources

Environment Variables:
    MIST_APITOKEN  - API token for authentication (required)
    MIST_HOST      - API host (default: api.mist.com)
    MIST_ORG_ID    - Organization ID (optional, auto-detected)
    org_id         - Legacy fallback for organization ID

Dependencies:
    - mistapi >= 0.59 (Mist API Python SDK by tmunzer)
    - Standard library: os, time, logging, typing

Author: Joseph Morrison <jmorrison@juniper.net>
License: MIT

Example:
    from mist_connection import MistConnection
    
    mist = MistConnection()
    result = mist.test_connection()
    if result["success"]:
        sites = mist.get_sites()
        for site in sites:
            health = mist.get_site_health(site["id"])
            print(f"{site['name']}: {health['summary']['health_percentage']}%")
"""

# =============================================================================
# IMPORTS
# =============================================================================

# Standard library imports
import os
import time
import logging
from typing import Dict, List, Optional, Any

# Third-party imports
# Juniper Mist API Python SDK (tmunzer/mistapi_python)
import mistapi

# =============================================================================
# MODULE CONFIGURATION
# =============================================================================

# Create module-level logger
# Inherits configuration from app.py when used as a module
logger = logging.getLogger(__name__)


# =============================================================================
# MIST CONNECTION CLASS
# =============================================================================


class MistConnection:
    """
    Manages connection to the Juniper Mist Cloud API.
    
    This class provides a high-level interface for interacting with the Mist Cloud
    API. It handles authentication, session management, and provides methods for
    retrieving various types of data from sites and organizations.
    
    Attributes:
        api_token (str): Mist API token for authentication
        api_host (str): Mist API host (e.g., api.mist.com, api.eu.mist.com)
        org_id (str): Organization ID to use for API calls
        session (mistapi.APISession): Cached API session object
    
    Note:
        The session is lazily initialized on first API call to improve
        startup performance. Organization ID is auto-detected if not provided.
    
    Example:
        mist = MistConnection()
        result = mist.test_connection()
        if result["success"]:
            print(f"Connected to: {result['org_name']}")
    """
    
    def __init__(self):
        """
        Initialize Mist API connection with credentials from environment.
        
        Reads configuration from environment variables:
            - MIST_APITOKEN: API token for authentication (required)
            - MIST_HOST: API host (default: api.mist.com)
            - MIST_ORG_ID: Organization ID (optional, auto-detected)
            - org_id: Legacy fallback for organization ID
        
        The session is not created during initialization to allow for
        configuration changes before the first API call.
        """
        # Read credentials from environment
        self.api_token = os.getenv("MIST_APITOKEN")
        self.api_host = os.getenv("MIST_HOST", "api.mist.com")
        
        # Support both MIST_ORG_ID and legacy org_id environment variables
        self.org_id = os.getenv("MIST_ORG_ID") or os.getenv("org_id")
        
        # Session is lazily initialized on first API call
        self.session = None
        
        # Warn if API token is not configured (will fail on first API call)
        if not self.api_token:
            logger.warning("MIST_APITOKEN not set in environment")
        
    def _get_session(self) -> mistapi.APISession:
        """
        Get or create an API session (lazy initialization).
        
        Implements lazy initialization of the API session. The session is
        created on first call and reused for subsequent requests to avoid
        repeated authentication overhead.
        
        Returns:
            mistapi.APISession: Authenticated session object for API calls
        
        Note:
            The session uses the configured api_host and api_token.
            If api_token is empty, API calls will fail with authentication errors.
        """
        if self.session is None:
            self.session = mistapi.APISession(
                host=self.api_host,
                apitoken=self.api_token or ""
            )
        return self.session
    
    # =========================================================================
    # CONNECTION AND ORGANIZATION METHODS
    # =========================================================================
    
    def test_connection(self) -> Dict[str, Any]:
        """
        Test the API connection and return organization info.
        
        This method verifies API connectivity by retrieving organization data.
        If org_id is configured, it fetches that specific organization. Otherwise,
        it uses the /self endpoint to discover the first available organization.
        
        Returns:
            dict: Connection result with the following structure:
                - success (bool): True if connection succeeded
                - org_name (str): Name of the organization (on success)
                - org_id (str): Organization ID (on success)
                - error (str): Error message (on failure)
        
        Side Effects:
            Sets self.org_id if auto-detected from API token privileges
        
        Example:
            result = mist.test_connection()
            if result["success"]:
                print(f"Connected to: {result['org_name']}")
            else:
                print(f"Error: {result['error']}")
        """
        try:
            session = self._get_session()
            
            # If org_id is configured, verify we can access it
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
                # Auto-detect organization from API token privileges
                response = mistapi.api.v1.self.self.getSelf(session)
                self_data = response.data if hasattr(response, "data") else response
                privileges = self_data.get("privileges", []) if isinstance(self_data, dict) else []
                
                if privileges:
                    # Use the first organization the token has access to
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
    
    # =========================================================================
    # SITE DATA METHODS
    # =========================================================================
    
    def get_sites(self) -> List[Dict[str, Any]]:
        """
        Get all sites in the organization.
        
        Retrieves a list of all sites associated with the configured organization.
        Sites are sorted alphabetically by name for consistent display in the UI.
        
        Returns:
            list: List of site dictionaries with the following fields:
                - id (str): Site UUID
                - name (str): Site name
                - address (str): Site address
                - country_code (str): Two-letter country code
                - timezone (str): Site timezone
        
        Raises:
            ValueError: If organization ID cannot be determined
            Exception: For API errors
        
        Note:
            If org_id is not set, this method will attempt to auto-detect it
            by calling test_connection() first.
        """
        try:
            session = self._get_session()
            
            # Ensure we have an org_id before proceeding
            if not self.org_id:
                test_result = self.test_connection()
                if not test_result["success"]:
                    raise ValueError("Could not determine organization ID")
            
            # org_id is guaranteed to be set after test_connection succeeds
            org_id: str = self.org_id or ""
            
            # Fetch all sites with pagination handled by mistapi.get_all()
            response = mistapi.api.v1.orgs.sites.listOrgSites(
                session, 
                org_id, 
                limit=1000  # Maximum items per page
            )
            sites = mistapi.get_all(response=response, mist_session=session) or []
            
            # Sort sites alphabetically by name for consistent UI display
            sites.sort(key=lambda site: site.get("name", "").lower())
            
            # Return simplified site list with only needed fields for dropdown
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
    
    # =========================================================================
    # DEVICE HEALTH METHODS
    # =========================================================================
    
    def get_site_health(self, site_id: str) -> Dict[str, Any]:
        """
        Get device health statistics for a site.
        
        Retrieves device statistics for all device types (APs, switches, gateways)
        and calculates health metrics including connected/disconnected counts
        and overall health percentage.
        
        This method also resolves SSID names for APs by building a mapping from
        device profiles to WLANs via templates. This allows displaying which
        SSIDs each AP is broadcasting.
        
        Args:
            site_id: UUID of the site to query
        
        Returns:
            dict: Health data with the following structure:
                {
                    "aps": {
                        "total": int,
                        "connected": int,
                        "disconnected": int,
                        "devices": [device_summary, ...]
                    },
                    "switches": {...},
                    "gateways": {...},
                    "summary": {
                        "total": int,
                        "connected": int,
                        "disconnected": int,
                        "health_percentage": float
                    }
                }
        
        Note:
            Device summaries include type-specific fields:
            - APs: num_clients, ssids, power_src, port_speed
            - Switches: num_wired_clients, num_wifi_clients, num_aps
            - Gateways: Basic device info only
        """
        try:
            session = self._get_session()
            org_id: str = self.org_id or ""
            
            # -----------------------------------------------------------------
            # Build device profile -> SSIDs mapping for AP SSID resolution
            # Chain: AP.deviceprofile_id -> Template.deviceprofile_ids -> WLAN.template_id
            # -----------------------------------------------------------------
            deviceprofile_ssids: Dict[str, List[str]] = {}
            try:
                # Fetch org templates to build template -> device profile mapping
                templates_response = mistapi.api.v1.orgs.templates.listOrgTemplates(
                    session, org_id
                )
                templates_data = templates_response.data if hasattr(templates_response, "data") else templates_response
                templates = templates_data if isinstance(templates_data, list) else []
                
                # Build template_id -> deviceprofile_ids mapping
                # Templates define which device profiles should receive which configs
                template_to_deviceprofiles: Dict[str, List[str]] = {}
                for template in templates:
                    if isinstance(template, dict):
                        template_id = template.get("id", "")
                        dp_ids = template.get("deviceprofile_ids", [])
                        filter_by_dp = template.get("filter_by_deviceprofile", False)
                        # Only include if template is filtered by device profile
                        if template_id and dp_ids and filter_by_dp:
                            template_to_deviceprofiles[template_id] = dp_ids
                
                # Fetch org WLANs to map SSIDs to device profiles
                wlans_response = mistapi.api.v1.orgs.wlans.listOrgWlans(
                    session, org_id
                )
                wlans_data = wlans_response.data if hasattr(wlans_response, "data") else wlans_response
                org_wlans = wlans_data if isinstance(wlans_data, list) else []
                
                # Build deviceprofile_id -> SSIDs mapping
                for wlan in org_wlans:
                    if not isinstance(wlan, dict):
                        continue
                    # Skip disabled WLANs
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
                            # Avoid duplicate SSIDs in the list
                            if ssid not in deviceprofile_ssids[dp_id]:
                                deviceprofile_ssids[dp_id].append(ssid)
                
                logger.debug(f"Built SSID mapping for {len(deviceprofile_ssids)} device profiles")
            except Exception as mapping_error:
                # Non-fatal: continue without SSID mapping if it fails
                logger.warning(f"Could not build device profile SSID mapping: {mapping_error}")
            
            # -----------------------------------------------------------------
            # Fetch device stats for all device types
            # -----------------------------------------------------------------
            response = mistapi.api.v1.sites.stats.listSiteDevicesStats(
                session, 
                site_id, 
                type="all",  # Fetch APs, switches, and gateways
                limit=1000
            )
            devices = mistapi.get_all(response=response, mist_session=session) or []
            
            # -----------------------------------------------------------------
            # Initialize health data structure
            # -----------------------------------------------------------------
            health_data = {
                "aps": {"total": 0, "connected": 0, "disconnected": 0, "devices": []},
                "switches": {"total": 0, "connected": 0, "disconnected": 0, "devices": []},
                "gateways": {"total": 0, "connected": 0, "disconnected": 0, "devices": []},
                "summary": {"total": 0, "connected": 0, "disconnected": 0, "health_percentage": 0}
            }
            
            # -----------------------------------------------------------------
            # Process each device and categorize by type
            # -----------------------------------------------------------------
            for device in devices:
                device_type = device.get("type", "unknown")
                status = device.get("status", "unknown")
                is_connected = status == "connected"
                
                # Build base device summary with common fields
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
                    # ---------------------------------------------------------
                    # AP-specific fields
                    # ---------------------------------------------------------
                    device_summary["num_clients"] = device.get("num_clients", 0) or 0
                    device_summary["power_src"] = device.get("power_src", "")
                    device_summary["power_opmode"] = device.get("power_opmode", "")
                    
                    # Get port speed from port_stat (eth0 is the uplink port)
                    port_stat = device.get("port_stat", {})
                    eth0_stat = port_stat.get("eth0", {})
                    device_summary["port_speed"] = eth0_stat.get("speed", 0)
                    
                    # Resolve SSIDs for this AP based on its device profile
                    # Uses the deviceprofile_id -> SSIDs mapping built from templates
                    ap_deviceprofile_id = device.get("deviceprofile_id", "")
                    ap_ssids: List[str] = deviceprofile_ssids.get(ap_deviceprofile_id, [])
                    device_summary["ssids"] = ap_ssids.copy() if ap_ssids else []
                    
                    # Update AP counters
                    health_data["aps"]["total"] += 1
                    health_data["aps"]["connected" if is_connected else "disconnected"] += 1
                    health_data["aps"]["devices"].append(device_summary)
                    
                elif device_type == "switch":
                    # ---------------------------------------------------------
                    # Switch-specific fields
                    # ---------------------------------------------------------
                    # Client stats are nested under clients_stats.total
                    clients_stats = device.get("clients_stats", {}).get("total", {})
                    device_summary["num_wired_clients"] = clients_stats.get("num_wired_clients", 0) or 0
                    device_summary["num_wifi_clients"] = clients_stats.get("num_wifi_clients", 0) or 0
                    
                    # num_aps comes as a list, get the first value
                    num_aps = clients_stats.get("num_aps", [0])
                    device_summary["num_aps"] = num_aps[0] if isinstance(num_aps, list) and num_aps else 0
                    
                    # Update switch counters
                    health_data["switches"]["total"] += 1
                    health_data["switches"]["connected" if is_connected else "disconnected"] += 1
                    health_data["switches"]["devices"].append(device_summary)
                    
                elif device_type == "gateway":
                    # ---------------------------------------------------------
                    # Gateway-specific fields (basic info only, WAN details separate)
                    # ---------------------------------------------------------
                    health_data["gateways"]["total"] += 1
                    health_data["gateways"]["connected" if is_connected else "disconnected"] += 1
                    health_data["gateways"]["devices"].append(device_summary)
                
                # Update overall summary counters
                health_data["summary"]["total"] += 1
                health_data["summary"]["connected" if is_connected else "disconnected"] += 1
            
            # -----------------------------------------------------------------
            # Calculate overall health percentage
            # -----------------------------------------------------------------
            if health_data["summary"]["total"] > 0:
                health_data["summary"]["health_percentage"] = round(
                    (health_data["summary"]["connected"] / health_data["summary"]["total"]) * 100, 1
                )
            
            return health_data
            
        except Exception as error:
            logger.error(f"Error fetching site health for {site_id}: {error}")
            raise
    
    # =========================================================================
    # SLE (SERVICE LEVEL EXPERIENCE) METHODS
    # =========================================================================
    
    def get_site_sle(self, site_id: str, duration: str = "1d") -> Dict[str, Any]:
        """
        Get SLE (Service Level Experience) metrics for a site with subcategories.
        
        SLE metrics provide insight into the quality of service experienced by
        users. This method fetches summary scores for all enabled metrics and
        categorizes them into WiFi, Wired, and WAN categories.
        
        The SLE value is calculated as: ((total - degraded) / total) * 100
        
        Args:
            site_id: The site ID to get SLE metrics for
            duration: Time range for metrics calculation
                      Valid values: '10m', '1h', 'today', '1d', '1w' (default: '1d')
        
        Returns:
            dict: SLE data with the following structure:
                {
                    "wifi": {
                        "metrics": {"coverage": 95.5, "capacity": 88.2, ...},
                        "available": True/False
                    },
                    "wired": {"metrics": {...}, "available": True/False},
                    "wan": {"metrics": {...}, "available": True/False}
                }
        
        Note:
            - For '10m' duration, explicit start/end timestamps are used
            - Metrics are only included if they have data (total_sum > 0)
            - Duplicate metric names (e.g., metric-v2 variants) are deduplicated
        """
        try:
            session = self._get_session()
            
            # -----------------------------------------------------------------
            # Calculate time range parameters
            # -----------------------------------------------------------------
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
            
            # -----------------------------------------------------------------
            # Initialize SLE data structure
            # -----------------------------------------------------------------
            sle_data = {
                "wifi": {"metrics": {}, "available": False},
                "wired": {"metrics": {}, "available": False},
                "wan": {"metrics": {}, "available": False}
            }
            
            # Define which metrics belong to which category
            # These are the standard SLE metric names from the Mist API
            metric_categories = {
                "wifi": ["coverage", "capacity", "time-to-connect", "roaming", "throughput", "ap-availability", "ap-health"],
                "wired": ["switch-health", "switch-throughput", "switch-stc"],
                "wan": ["gateway-health", "wan-link-health", "application-health", "gateway-bandwidth"]
            }
            
            # -----------------------------------------------------------------
            # Get list of enabled metrics for the site
            # -----------------------------------------------------------------
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
            
            # -----------------------------------------------------------------
            # Fetch summary for each enabled metric and categorize
            # -----------------------------------------------------------------
            for metric in enabled_metrics:
                # Determine which category this metric belongs to
                category = None
                for cat, cat_metrics in metric_categories.items():
                    if metric in cat_metrics or any(metric.startswith(m) for m in cat_metrics):
                        category = cat
                        break
                
                # Skip metrics that don't belong to any category
                if not category:
                    continue
                    
                try:
                    # ---------------------------------------------------------
                    # Fetch SLE summary for this metric
                    # ---------------------------------------------------------
                    # Note: Using getSiteSleSummaryTrend instead of deprecated getSiteSleSummary
                    # The API returns sample data that must be aggregated to calculate SLE
                    if use_timestamps:
                        # Use explicit start/end timestamps for short durations (10m)
                        # API expects epoch timestamps as strings, not integers
                        summary_response = mistapi.api.v1.sites.sle.getSiteSleSummaryTrend(
                            session,
                            site_id,
                            scope="site",
                            scope_id=site_id,
                            metric=metric,
                            start=str(start_time),
                            end=str(end_time)
                        )
                    else:
                        # Use duration parameter for standard time ranges
                        summary_response = mistapi.api.v1.sites.sle.getSiteSleSummaryTrend(
                            session,
                            site_id,
                            scope="site",
                            scope_id=site_id,
                            metric=metric,
                            duration=api_duration
                        )
                    summary_data = summary_response.data if hasattr(summary_response, "data") else summary_response
                    
                    # ---------------------------------------------------------
                    # Calculate SLE percentage from sample data
                    # ---------------------------------------------------------
                    # Formula: SLE % = ((total - degraded) / total) * 100
                    # The API returns arrays of sample values that need to be summed
                    if isinstance(summary_data, dict) and "sle" in summary_data:
                        sle_info = summary_data.get("sle", {})
                        samples = sle_info.get("samples", {})
                        total_samples = samples.get("total", [])
                        degraded_samples = samples.get("degraded", [])
                        
                        # Sum up totals and degraded values (filter out None values)
                        total_sum = sum([x for x in total_samples if x is not None])
                        degraded_sum = sum([x for x in degraded_samples if x is not None])
                        
                        # Only record metrics with actual data
                        if total_sum > 0:
                            sle_value = ((total_sum - degraded_sum) / total_sum) * 100
                            sle_data[category]["available"] = True
                            
                            # Clean up metric name for display (remove version suffixes)
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
        """
        Get list of classifiers for a specific SLE metric.
        
        Classifiers break down an SLE metric into its contributing factors.
        For example, the 'coverage' metric might have classifiers like
        'weak-signal', 'interference', 'ap-disconnected', etc.
        
        Args:
            site_id: The site ID
            metric: The SLE metric name (e.g., 'coverage', 'capacity', 'time-to-connect')
            scope: The scope type - determines what level of detail is returned
                   Values: 'site', 'ap', 'switch', 'gateway', 'client'
        
        Returns:
            List of classifier dictionaries with structure:
            [
                {"name": "weak-signal", "impact": {...}},
                {"name": "interference", "impact": {...}},
                ...
            ]
        """
        try:
            session = self._get_session()
            
            # Fetch classifier list from the Mist SLE API
            response = mistapi.api.v1.sites.sle.listSiteSleMetricClassifiers(
                session,
                site_id,
                scope=scope,
                scope_id=site_id,
                metric=metric
            )
            data = response.data if hasattr(response, "data") else response
            
            # Extract classifiers array from response
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
        """
        Get detailed breakdown for a specific SLE classifier.
        
        Provides granular data about a specific factor affecting SLE scores.
        This is used for drill-down views when users click on a classifier
        in the SLE detail pages.
        
        Args:
            site_id: The site ID
            metric: The SLE metric name (e.g., 'coverage', 'capacity')
            classifier: The classifier name (e.g., 'weak-signal', 'interference')
            duration: Time range for data aggregation
                      Values: '1h', '1d', '1w' (default: '1d')
            scope: The scope type for the query
                   Values: 'site', 'ap', 'switch', 'gateway', 'client'
        
        Returns:
            dict: Classifier details including:
                - samples: Time-series data for the classifier
                - impact: Number of affected clients/connections
                - distribution: Breakdown by severity or cause
        """
        try:
            session = self._get_session()
            
            # Fetch detailed classifier breakdown from the Mist API
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
        """
        Get impact summary showing affected clients/devices for an SLE metric.
        
        Impact summaries help quantify how many users or devices are affected
        by SLE degradation. Used to prioritize which issues to address first.
        
        Args:
            site_id: The site ID
            metric: The SLE metric name (e.g., 'coverage', 'throughput')
            duration: Time range for impact calculation
                      Values: '1h', '1d', '1w' (default: '1d')
            classifier: Optional classifier to filter by (e.g., 'weak-signal')
                        If None, returns overall metric impact
            scope: The scope type for the query
                   Values: 'site', 'ap', 'switch', 'gateway', 'client'
        
        Returns:
            dict: Impact summary data including:
                - total_clients: Number of clients affected
                - total_connections: Number of affected connection attempts
                - by_wlan: (for WiFi) breakdown by WLAN
                - by_ap: (for WiFi) breakdown by AP
        """
        try:
            session = self._get_session()
            
            # Build dynamic kwargs to handle optional classifier parameter
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
            
            # Fetch impact summary from the Mist API
            response = mistapi.api.v1.sites.sle.getSiteSleImpactSummary(**kwargs)
            data = response.data if hasattr(response, "data") else response
            
            return data if isinstance(data, dict) else {}
            
        except Exception as error:
            logger.error(f"Error fetching SLE impact summary for {metric}: {error}")
            return {}
    
    def get_sle_impacted_items(
        self,
        site_id: str,
        metric: str,
        item_type: str,
        duration: str = "1d",
        classifier: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Get detailed impacted items (Distribution/Affected Items) for an SLE metric.
        
        Retrieves rich distribution data showing which gateways, interfaces,
        applications, or clients are affected by SLE degradation. This matches
        the data shown in Mist dashboard's Distribution and Affected Items tabs.
        
        Args:
            site_id: The site ID
            metric: SLE metric name (e.g., 'wan-link-health', 'gateway-health')
            item_type: Type of impacted items to retrieve. Valid values:
                - 'gateways': WAN Edges with failure rates
                - 'interfaces': Gateway interfaces with failure rates
                - 'applications': Applications with failure rates
                - 'clients': Wired clients affected
                - 'wireless_clients': Wireless clients affected
            duration: Time range ('1d', '7d', '2w')
            classifier: Optional classifier filter (e.g., 'network-latency')
        
        Returns:
            dict: Contains metadata and items list with failure rates:
            {
                "total_count": 12,
                "items": [
                    {"name": "ge-0/0/1", "degraded": 100, "total": 500, "failure_rate": 20.0, ...}
                ]
            }
        """
        try:
            session = self._get_session()
            
            # Build kwargs for API call
            base_kwargs: Dict[str, Any] = {
                "mist_session": session,
                "site_id": site_id,
                "scope": "site",
                "scope_id": site_id,
                "metric": metric,
                "duration": duration
            }
            if classifier:
                base_kwargs["classifier"] = classifier
            
            # Map item_type to appropriate API function and response key
            api_map = {
                "gateways": (mistapi.api.v1.sites.sle.listSiteSleImpactedGateways, "gateways"),
                "interfaces": (mistapi.api.v1.sites.sle.listSiteSleImpactedInterfaces, "interfaces"),
                "applications": (mistapi.api.v1.sites.sle.listSiteSleImpactedApplications, "apps"),
                "clients": (mistapi.api.v1.sites.sle.listSiteSleImpactedWiredClients, "clients"),
                "wireless_clients": (mistapi.api.v1.sites.sle.listSiteSleImpactedWirelessClients, "clients"),
            }
            
            if item_type not in api_map:
                return {"total_count": 0, "items": [], "error": f"Invalid item_type: {item_type}"}
            
            api_func, response_key = api_map[item_type]
            response = api_func(**base_kwargs)
            data = response.data if hasattr(response, "data") else response
            
            if not isinstance(data, dict):
                return {"total_count": 0, "items": []}
            
            raw_items = data.get(response_key, [])
            total_count = data.get("total_count", len(raw_items))
            
            # Calculate failure rate and overall impact for each item
            total_degraded_all = sum(item.get("degraded", 0) for item in raw_items)
            
            items = []
            for item in raw_items:
                degraded = item.get("degraded", 0)
                total = item.get("total", 0)
                failure_rate = round((degraded / total) * 100, 1) if total > 0 else 0
                overall_impact = round((degraded / total_degraded_all) * 100, 1) if total_degraded_all > 0 else 0
                
                processed_item = {
                    **item,
                    "failure_rate": failure_rate,
                    "overall_impact": overall_impact
                }
                items.append(processed_item)
            
            # Sort by overall_impact descending (most impactful first)
            items.sort(key=lambda x: x.get("overall_impact", 0), reverse=True)
            
            return {
                "total_count": total_count,
                "metric": metric,
                "classifier": classifier or "",
                "items": items
            }
            
        except Exception as error:
            logger.error(f"Error fetching impacted {item_type} for {metric}: {error}")
            return {"total_count": 0, "items": [], "error": str(error)}
    
    def get_sle_details(self, site_id: str, category: str, duration: str = "1d") -> Dict[str, Any]:
        """
        Get comprehensive SLE details for a category (wifi, wired, or wan).
        
        This method aggregates multiple API calls to build a complete picture
        of SLE health for a given category. Used by the detail pages to show:
        - All metrics in the category with their current scores
        - Classifiers contributing to each metric's degradation
        - Impact data showing affected clients/devices
        
        Args:
            site_id: The site ID
            category: SLE category to fetch
                      Values: 'wifi', 'wired', 'wan'
            duration: Time range for data aggregation
                      Values: '1h', '1d', '1w' (default: '1d')
        
        Returns:
            dict: Comprehensive SLE data:
            {
                "metrics": {
                    "coverage": {
                        "value": 95.5,
                        "classifiers": [
                            {"name": "weak-signal", "impact": 15, ...},
                            ...
                        ]
                    },
                    ...
                },
                "available": True/False
            }
        """
        try:
            session = self._get_session()
            
            # -----------------------------------------------------------------
            # Define metric-to-category mapping
            # -----------------------------------------------------------------
            metric_categories = {
                "wifi": ["coverage", "capacity", "time-to-connect", "roaming", "throughput", "ap-availability", "ap-health"],
                "wired": ["switch-health", "switch-throughput", "switch-stc"],
                "wan": ["gateway-health", "wan-link-health", "application-health", "gateway-bandwidth"]
            }
            
            if category not in metric_categories:
                raise ValueError(f"Invalid category: {category}")
            
            # -----------------------------------------------------------------
            # Get list of enabled metrics for the site
            # -----------------------------------------------------------------
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
            
            # -----------------------------------------------------------------
            # Filter to only metrics in this category that are enabled
            # -----------------------------------------------------------------
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
                
                # Get SLE summary trend - this includes classifiers with full data
                # Using getSiteSleSummaryTrend instead of deprecated getSiteSleSummary
                try:
                    summary_response = mistapi.api.v1.sites.sle.getSiteSleSummaryTrend(
                        session,
                        site_id,
                        scope="site",
                        scope_id=site_id,
                        metric=actual_metric,
                        duration=duration
                    )
                    summary_data = summary_response.data if hasattr(summary_response, "data") else summary_response
                    
                    # Also get the deprecated summary to extract impact data per classifier
                    # The trend API doesn't include per-classifier impact, only the deprecated one does
                    classifier_impact_map: Dict[str, Dict[str, Any]] = {}
                    try:
                        impact_response = mistapi.api.v1.sites.sle.getSiteSleSummary(
                            session,
                            site_id,
                            scope="site",
                            scope_id=site_id,
                            metric=actual_metric,
                            duration=duration
                        )
                        impact_data = impact_response.data if hasattr(impact_response, "data") else impact_response
                        if isinstance(impact_data, dict):
                            for clf in impact_data.get("classifiers", []):
                                if isinstance(clf, dict) and "name" in clf and "impact" in clf:
                                    classifier_impact_map[clf["name"]] = clf["impact"]
                    except Exception as e:
                        logger.debug(f"Could not get impact data from deprecated API for {actual_metric}: {e}")
                    
                    if isinstance(summary_data, dict):
                        # Extract overall SLE value
                        if "sle" in summary_data:
                            sle_info = summary_data.get("sle", {})
                            samples = sle_info.get("samples", {})
                            total = sum(x for x in samples.get("total", []) if x is not None)
                            degraded = sum(x for x in samples.get("degraded", []) if x is not None)
                            
                            if total > 0:
                                metric_data["sle_value"] = round(((total - degraded) / total) * 100, 1)
                        
                        # Extract overall impact
                        metric_data["impact"] = summary_data.get("impact", {})
                        
                        # Extract classifiers from summary (already contains full data)
                        raw_classifiers = summary_data.get("classifiers", [])
                        
                        # Calculate total degraded across all classifiers for percentage calc
                        total_classifier_degraded = 0
                        for clf in raw_classifiers:
                            if isinstance(clf, dict):
                                clf_samples = clf.get("samples", {})
                                clf_degraded = clf_samples.get("degraded", [])
                                total_classifier_degraded += sum(x for x in clf_degraded if x is not None)
                        
                        # Process each classifier
                        for clf in raw_classifiers:
                            if isinstance(clf, dict):
                                clf_name = clf.get("name", "unknown")
                                clf_samples = clf.get("samples", {})
                                # Get impact from the deprecated API's classifier data
                                clf_impact = classifier_impact_map.get(clf_name, {})
                                
                                # Calculate degraded sum for this classifier
                                degraded_values = clf_samples.get("degraded", [])
                                clf_degraded_sum = sum(x for x in degraded_values if x is not None)
                                
                                # Calculate percentage of total degradation
                                percentage = 0
                                if total_classifier_degraded > 0:
                                    percentage = round((clf_degraded_sum / total_classifier_degraded) * 100, 1)
                                
                                # Build impact dict with all available fields
                                # WiFi uses: num_aps, total_aps, num_users, total_users
                                # WAN uses: num_gateways, total_gateways, num_users, total_users
                                # Wired uses: num_switches, total_switches
                                classifier_info = {
                                    "name": clf_name,
                                    "degraded_sum": clf_degraded_sum,
                                    "percentage": percentage,
                                    "impact": {
                                        "num_aps": clf_impact.get("num_aps", 0) or 0,
                                        "total_aps": clf_impact.get("total_aps", 0) or 0,
                                        "num_gateways": clf_impact.get("num_gateways", 0) or 0,
                                        "total_gateways": clf_impact.get("total_gateways", 0) or 0,
                                        "num_switches": clf_impact.get("num_switches", 0) or 0,
                                        "total_switches": clf_impact.get("total_switches", 0) or 0,
                                        "num_users": clf_impact.get("num_users", 0) or 0,
                                        "total_users": clf_impact.get("total_users", 0) or 0
                                    },
                                    "samples": clf_samples
                                }
                                
                                # Only include classifiers with actual degradation
                                if clf_degraded_sum > 0:
                                    metric_data["classifiers"].append(classifier_info)
                        
                        # Sort classifiers by percentage (highest first)
                        metric_data["classifiers"].sort(key=lambda x: x.get("percentage", 0), reverse=True)
                        
                except Exception as e:
                    logger.debug(f"Could not get summary for {actual_metric}: {e}")
                
                result["metrics"][metric] = metric_data
            
            return result
            
        except Exception as error:
            logger.error(f"Error fetching SLE details for {category}: {error}")
            raise

    def get_classifier_impact_details(
        self, site_id: str, metric: str, classifier: str, duration: str = "1d"
    ) -> Dict[str, Any]:
        """
        Get detailed impact information for a specific classifier.
        
        Provides granular breakdown of which network elements are affected by
        a specific SLE degradation factor. This data is used for root cause
        analysis and remediation planning.
        
        Args:
            site_id: The site ID
            metric: The SLE metric name (e.g., 'coverage', 'capacity')
            classifier: The classifier name (e.g., 'weak-signal', 'interference')
            duration: Time range for impact data
                      Values: '1h', '1d', '1w' (default: '1d')
        
        Returns:
            dict: Detailed impact breakdown:
            {
                "metric": "coverage",
                "classifier": "weak-signal",
                "aps": [{"mac": "...", "name": "...", "degraded": 100, "total": 500}, ...],
                "wlans": [{"id": "...", "name": "Corp WiFi", "degraded": 50, ...}, ...],
                "device_types": [{"type": "laptop", "degraded": 75, ...}, ...],
                "device_os": [{"os": "Windows", "degraded": 60, ...}, ...],
                "bands": [{"band": "2.4 GHz", "degraded": 80, ...}, ...]
            }
            
        Note:
            All lists are sorted by 'degraded' count (highest first) for
            easy identification of the most impacted elements.
        """
        try:
            session = self._get_session()
            
            # Fetch impact summary from the Mist API
            response = mistapi.api.v1.sites.sle.getSiteSleImpactSummary(
                session,
                site_id,
                scope="site",
                scope_id=site_id,
                metric=metric,
                classifier=classifier,
                duration=duration
            )
            
            data = response.data if hasattr(response, "data") else response
            
            # Return empty structure if no valid data
            if not isinstance(data, dict):
                return {"aps": [], "wlans": [], "device_types": [], "device_os": [], "bands": []}
            
            # -----------------------------------------------------------------
            # Process APs - extract only those with actual degradation
            # -----------------------------------------------------------------
            aps = []
            for ap in data.get("ap", []):
                if ap.get("degraded", 0) > 0:
                    aps.append({
                        "mac": ap.get("ap_mac", ""),
                        "name": ap.get("name", ap.get("ap_mac", "Unknown")),
                        "degraded": ap.get("degraded", 0),
                        "total": ap.get("total", 0)
                    })
            aps.sort(key=lambda x: x["degraded"], reverse=True)
            
            # -----------------------------------------------------------------
            # Process WLANs - extract only those with actual degradation
            # -----------------------------------------------------------------
            wlans = []
            for wlan in data.get("wlan", []):
                if wlan.get("degraded", 0) > 0:
                    wlans.append({
                        "id": wlan.get("wlan_id", ""),
                        "name": wlan.get("name", "Unknown"),
                        "degraded": wlan.get("degraded", 0),
                        "total": wlan.get("total", 0)
                    })
            wlans.sort(key=lambda x: x["degraded"], reverse=True)
            
            # -----------------------------------------------------------------
            # Process device types (laptop, phone, tablet, etc.)
            # -----------------------------------------------------------------
            device_types = []
            for dt in data.get("device_type", []):
                if dt.get("degraded", 0) > 0:
                    device_types.append({
                        "type": dt.get("device_type", dt.get("name", "Unknown")),
                        "name": dt.get("name", "Unknown"),
                        "degraded": dt.get("degraded", 0),
                        "total": dt.get("total", 0)
                    })
            device_types.sort(key=lambda x: x["degraded"], reverse=True)
            
            # -----------------------------------------------------------------
            # Process device operating systems
            # -----------------------------------------------------------------
            device_os = []
            for dos in data.get("device_os", []):
                if dos.get("degraded", 0) > 0:
                    device_os.append({
                        "os": dos.get("device_os", dos.get("name", "Unknown")),
                        "name": dos.get("name", "Unknown"),
                        "degraded": dos.get("degraded", 0),
                        "total": dos.get("total", 0)
                    })
            device_os.sort(key=lambda x: x["degraded"], reverse=True)
            
            # -----------------------------------------------------------------
            # Process WiFi bands with human-readable names
            # -----------------------------------------------------------------
            bands = []
            for band in data.get("band", []):
                if band.get("degraded", 0) > 0:
                    # Convert band identifiers to human-readable format
                    band_name = band.get("band", band.get("name", "Unknown"))
                    if band_name == "24":
                        band_name = "2.4 GHz"
                    elif band_name == "5":
                        band_name = "5 GHz"
                    elif band_name == "6":
                        band_name = "6 GHz"
                    bands.append({
                        "band": band_name,
                        "degraded": band.get("degraded", 0),
                        "total": band.get("total", 0)
                    })
            bands.sort(key=lambda x: x["degraded"], reverse=True)
            
            return {
                "metric": metric,
                "classifier": classifier,
                "aps": aps,
                "wlans": wlans,
                "device_types": device_types,
                "device_os": device_os,
                "bands": bands
            }
            
        except Exception as error:
            logger.error(f"Error fetching classifier impact details: {error}")
            return {"aps": [], "wlans": [], "device_types": [], "device_os": [], "bands": []}
    
    # =========================================================================
    # DEVICE AND CLIENT METHODS
    # =========================================================================
    
    def get_site_devices(self, site_id: str, device_type: str = "all") -> List[Dict[str, Any]]:
        """
        Get detailed device information for a site.
        
        Fetches device statistics including status, model, version, and
        client counts for all network devices at a site.
        
        Args:
            site_id: The site ID
            device_type: Filter by device type
                         Values: 'all', 'ap', 'switch', 'gateway' (default: 'all')
        
        Returns:
            list: Device dictionaries with fields:
                - id, name, mac, model, serial, status
                - type: 'ap', 'switch', or 'gateway'
                - ip, version, uptime, last_seen
                - num_clients: (APs) connected client count
        """
        try:
            session = self._get_session()
            
            # Fetch device stats with optional type filter
            response = mistapi.api.v1.sites.stats.listSiteDevicesStats(
                session, 
                site_id, 
                type=device_type, 
                limit=1000
            )
            # Use pagination to get all devices if more than 1000
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
        """
        Get wireless client data combining sessions, client stats, and client search.
        
        This provides the most complete view of wireless clients by merging data
        from three different Mist API endpoints:
        
        1. listSiteWirelessClientsStats - Current connected clients with real-time
           statistics (RSSI, uptime, etc.)
        2. searchSiteWirelessClients - Client search with hostname, username, OS
           and other identification data
        3. searchSiteWirelessClientSessions - Historical session data including
           connect/disconnect times and duration
        
        The method deduplicates clients by MAC address and merges fields from
        all sources to provide the most complete client profile possible.
        
        Args:
            site_id: The site ID to get wireless clients for
        
        Returns:
            list: Client dictionaries with merged data:
                - mac: Client MAC address (unique identifier)
                - hostname: DHCP hostname or empty string
                - ip: Current or last known IP address
                - username: 802.1X username if available
                - ssid: Connected SSID
                - ap: AP MAC address serving this client
                - band: WiFi band (2.4/5/6 GHz)
                - os: Operating system detected
                - manufacture: Device manufacturer
                - rssi: Signal strength (dBm)
                - is_connected: True if currently connected
                - last_seen: Unix timestamp of last activity
                - connect, disconnect, duration: Session timing
        """
        try:
            session = self._get_session()
            clients_by_mac = {}
            
            # -----------------------------------------------------------------
            # Source 1: Real-time connected clients with detailed stats
            # -----------------------------------------------------------------
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
            
            # -----------------------------------------------------------------
            # Source 2: Client search data (hostname, username, OS, etc.)
            # -----------------------------------------------------------------
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
                            # Merge with existing data - prefer non-empty values
                            existing = clients_by_mac[mac]
                            existing["hostname"] = existing.get("hostname") or client.get("last_hostname", "")
                            existing["ip"] = existing.get("ip") or client.get("last_ip", "")
                            existing["username"] = existing.get("username") or client.get("last_username", "")
                            existing["os"] = existing.get("os") or client.get("last_os", "")
                            existing["ssid"] = existing.get("ssid") or client.get("last_ssid", "")
                        else:
                            # New client not seen in stats (likely disconnected)
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
            
            # -----------------------------------------------------------------
            # Source 3: Session history (connect/disconnect times, duration)
            # -----------------------------------------------------------------
            try:
                sessions_response = mistapi.api.v1.sites.clients.searchSiteWirelessClientSessions(
                    session, 
                    site_id,
                    duration="7d",  # Look back 7 days for historical sessions
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
                            # Client only found in session history
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
        """
        Get wired client information combining search and stats.
        
        Retrieves wired (Ethernet-connected) client data from the Mist API
        including DHCP information, switch port details, and connection status.
        
        Uses searchSiteWiredClients API which provides:
        - DHCP hostname and vendor class identifier
        - IP addresses from DHCP
        - Switch MAC and port information
        - Connection timestamps
        
        Args:
            site_id: The site ID to get wired clients for
        
        Returns:
            list: Client dictionaries containing:
                - mac: Client MAC address (unique identifier)
                - hostname: DHCP hostname or FQDN
                - ip: Assigned IP address
                - username: 802.1X username if authenticated
                - connected_time: Unix timestamp when connection started
                - last_seen: Unix timestamp of last activity
                - device_type: DHCP fingerprint or vendor class
                - is_connected: True if seen within last 5 minutes
                - switch_mac: MAC of switch serving this client
                - port_id: Switch port identifier
        
        Note:
            Connection status is inferred from last_seen timestamp.
            Clients seen within the last 5 minutes are considered connected.
        """
        try:
            session = self._get_session()
            clients_by_mac = {}
            current_time = int(time.time())
            
            # -----------------------------------------------------------------
            # Get wired clients from search API
            # -----------------------------------------------------------------
            try:
                response = mistapi.api.v1.sites.wired_clients.searchSiteWiredClients(
                    session, 
                    site_id,
                    duration="7d",  # Look back 7 days for historical data
                    limit=1000
                )
                all_results = mistapi.get_all(response=response, mist_session=session) or []
                
                for client in all_results:
                    mac = client.get("mac", "")
                    if mac:
                        # Extract port info from device_mac_port array
                        device_mac_port = client.get("device_mac_port", [])
                        ip_list = client.get("ip", [])
                        port_info = {}
                        
                        if device_mac_port and isinstance(device_mac_port, list) and len(device_mac_port) > 0:
                            port_info = device_mac_port[0] if isinstance(device_mac_port[0], dict) else {}
                        
                        # ---------------------------------------------------------
                        # Extract best available IP address
                        # ---------------------------------------------------------
                        ip = ""
                        if isinstance(ip_list, list) and ip_list:
                            ip = ip_list[0]
                        elif isinstance(ip_list, str):
                            ip = ip_list
                        elif port_info.get("ip"):
                            ip = port_info.get("ip", "")
                        
                        # ---------------------------------------------------------
                        # Parse timestamps for connection timing
                        # ---------------------------------------------------------
                        timestamp = client.get("timestamp", 0)
                        port_start = port_info.get("start", 0)
                        
                        # Ensure timestamps are valid integers (API may return strings)
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
                        
                        # ---------------------------------------------------------
                        # Determine connection status
                        # ---------------------------------------------------------
                        # Consider connected if seen within last 5 minutes
                        last_seen = timestamp if timestamp else port_start
                        is_connected = (current_time - last_seen) < 300 if last_seen else False
                        
                        # Use port_start as connected_time if available
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
        """
        Get gateway device stats including WAN port information, VPN peers, and BGP peers.
        
        Provides comprehensive gateway status including:
        - Basic gateway info (model, version, uptime)
        - WAN port status and statistics
        - VPN peer connectivity (Mist tunnels, IPsec)
        - BGP peering status
        
        This is the main data source for the Gateway WAN Status page.
        
        Args:
            site_id: The site ID to get gateway status for
        
        Returns:
            list: Gateway dictionaries containing:
                - id, name, mac, model, serial, status, version, uptime
                - ext_ip: External/public IP address
                - wan_ports: List of WAN port status dictionaries
                    - name, wan_name, status, ip, wan_type
                    - rx_bytes, tx_bytes, rx_pkts, tx_pkts
                - vpn_peers: List of VPN peer status dictionaries
                    - vpn_name, vpn_role, type, peer_router_name
                    - up, is_active, latency, jitter, loss, mos
                - bgp_peers: List of BGP peer status dictionaries
                    - neighbor, neighbor_as, local_as, state
                    - up, rx_routes, tx_routes, uptime
        
        Note:
            VPN and BGP peer data is fetched separately using org-level
            stats APIs (searchOrgPeerPathStats, searchOrgBgpStats).
        """
        try:
            session = self._get_session()
            
            # Ensure org_id is set (required for VPN/BGP queries)
            if not self.org_id:
                test_result = self.test_connection()
                if not test_result["success"]:
                    raise ValueError("Could not determine organization ID")
            
            # -----------------------------------------------------------------
            # Fetch gateway device stats
            # -----------------------------------------------------------------
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
                
                # Build base gateway info structure
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
                
                # -------------------------------------------------------------
                # Extract WAN port information from if_stat
                # -------------------------------------------------------------
                # Only include ports where port_usage == "wan" or wan_type is set
                if_stat = gw.get("if_stat", {})
                
                for port_name, port_stats in if_stat.items():
                    if isinstance(port_stats, dict):
                        port_usage = port_stats.get("port_usage", "")
                        wan_type = port_stats.get("wan_type", "")
                        
                        # Filter to WAN ports only
                        if port_usage == "wan" or wan_type:
                            # Get IP from ips array (first IP)
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
                
                # -------------------------------------------------------------
                # Fetch VPN peers for this gateway
                # -------------------------------------------------------------
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
                
                # -------------------------------------------------------------
                # Fetch BGP peers for this gateway
                # -------------------------------------------------------------
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

    def get_org_sle_insights(
        self,
        sle_type: str,
        duration: str = "1d",
        limit: int = 100
    ) -> Dict[str, Any]:
        """
        Get org-wide SLE insights for a category (wifi, wired, wan), sorted by worst performers.
        
        Retrieves SLE (Service Level Experience) data for all sites in the organization
        for a specific SLE category, enabling identification of worst-performing sites.
        Uses the GET /api/v1/orgs/{org_id}/insights/sites-sle endpoint.
        
        Args:
            sle_type: SLE category to retrieve. Valid values: "wifi", "wired", "wan"
            duration: Time range for aggregation. Valid values: "1d", "7d", "2w"
            limit: Maximum number of sites to return (default: 100)
                
        Returns:
            Dict with keys:
                - success (bool): Whether API call succeeded
                - sle_type (str): The requested SLE category
                - duration (str): The time range used
                - sites (list): List of site data with all SLE metrics for the category
                    WiFi sites contain: site_id, site_name, num_aps, num_clients,
                        ap-availability, ap-health, capacity, coverage, roaming,
                        successful-connect, throughput, time-to-connect
                    Wired sites contain: site_id, site_name, num_switches, num_clients,
                        switch-health, switch-throughput, switch-bandwidth
                    WAN sites contain: site_id, site_name, num_gateways, num_clients,
                        application_health, gateway-health, wan-link-health
                - error (str): Error message if success is False
                
        Raises:
            Exception: If API call fails or session cannot be established
            
        Example:
            insights = mist.get_org_sle_insights("wifi", "1d")
            for site in insights["sites"][:10]:
                print(f"{site['site_name']}: coverage={site.get('coverage', 0):.1%}")
        """
        # Validate sle_type parameter
        valid_types = ["wifi", "wired", "wan"]
        if sle_type not in valid_types:
            return {
                "success": False,
                "sle_type": sle_type,
                "duration": duration,
                "sites": [],
                "error": f"Invalid sle_type '{sle_type}'. Must be one of: {valid_types}"
            }
        
        try:
            session = self._get_session()
            
            # Ensure we have an org_id before proceeding
            if not self.org_id:
                test_result = self.test_connection()
                if not test_result["success"]:
                    return {
                        "success": False,
                        "sle_type": sle_type,
                        "duration": duration,
                        "sites": [],
                        "error": "Could not determine organization ID"
                    }
            
            # org_id is guaranteed to be set after test_connection succeeds
            org_id: str = self.org_id or ""
            
            logger.info(f"Fetching org SLE insights for type '{sle_type}' (duration: {duration})")
            
            # Fetch all sites for name resolution (paginate to get all sites)
            site_name_map = {}
            page = 1
            while True:
                sites_response = mistapi.api.v1.orgs.sites.listOrgSites(
                    session, org_id, limit=1000, page=page
                )
                if sites_response and hasattr(sites_response, "data"):
                    sites_data = sites_response.data or []
                    if not sites_data:
                        break  # No more sites
                    for site in sites_data:
                        site_id = site.get("id", "")
                        site_name = site.get("name", "Unknown Site")
                        site_name_map[site_id] = site_name
                    # Check if there are more pages
                    if len(sites_data) < 1000:
                        break  # Last page
                    page += 1
                else:
                    break
            
            logger.debug(f"Built site name map with {len(site_name_map)} sites")
            
            # Use worst-sites-by-sle endpoint which returns sites sorted by worst performers
            # API: GET /api/v1/orgs/{org_id}/insights/worst-sites-by-sle
            # The sle parameter accepts category names: "wireless", "wired", "wan"
            # all_sle=true (default) returns all metrics in the category
            import time
            end_time = int(time.time())
            duration_seconds = {
                "1d": 86400,
                "7d": 604800,
                "2w": 1209600
            }
            start_time = end_time - duration_seconds.get(duration, 86400)
            
            # Map frontend category names to representative metrics
            # API doesn't accept category names like "wireless" - must use actual metrics
            # Using all_sle=true (default) returns all metrics in the same category
            sle_metric_map = {
                "wifi": "ap-availability",     # Returns all WiFi metrics
                "wired": "switch-stc",         # Returns all wired metrics (switch-health may return 0 results)
                "wan": "gateway-health"        # Returns all WAN metrics
            }
            sle_metric = sle_metric_map.get(sle_type, "ap-availability")
            
            # API supports limit param (undocumented, default is 10)
            uri = f"/api/v1/orgs/{org_id}/insights/worst-sites-by-sle"
            # mist_get expects query as a separate dict with string values
            query_params = {
                "sle": sle_metric,
                "start": str(start_time),
                "end": str(end_time),
                "limit": str(limit)
            }
            
            response = session.mist_get(uri, query=query_params)
            
            sites_list = []
            if response and response.status_code == 200:
                data = response.data if hasattr(response, "data") else {}
                results = data.get("results", []) if isinstance(data, dict) else data
                
                for site_data in results:
                    site_id = site_data.get("site_id", "")
                    site_name = site_name_map.get(site_id, "Unknown Site")
                    
                    # Build site entry with all available SLE metrics
                    site_entry = {
                        "site_id": site_id,
                        "site_name": site_name
                    }
                    
                    # Copy all SLE metrics from API response
                    # WiFi metrics: ap-availability, ap-health, capacity, coverage, roaming,
                    #               successful-connect, throughput, time-to-connect, num_aps, num_clients
                    # Wired metrics: switch-health, switch-throughput, switch-bandwidth,
                    #                num_switches, num_clients
                    # WAN metrics: application_health, gateway-health, wan-link-health,
                    #              num_gateways, num_clients
                    for key, value in site_data.items():
                        if key != "site_id":
                            site_entry[key] = value
                    
                    sites_list.append(site_entry)
            elif response:
                logger.warning(f"API returned status {response.status_code} for worst-sites-by-sle")
            
            # Apply client-side limit since API doesn't support limit parameter
            sites_limited = sites_list[:limit]
            
            logger.info(f"Retrieved {len(sites_list)} worst sites, returning top {len(sites_limited)} for category '{sle_type}'")
            
            return {
                "success": True,
                "sle_type": sle_type,
                "duration": duration,
                "sites": sites_limited,
                "total_sites": len(sites_limited)
            }
            
        except Exception as error:
            logger.error(f"Error fetching org SLE insights for type '{sle_type}': {error}")
            return {
                "success": False,
                "sle_type": sle_type,
                "duration": duration,
                "sites": [],
                "error": str(error)
            }

    def get_org_worst_sites_by_metric(
        self,
        metric: str,
        duration: str = "1d",
        limit: int = 100
    ) -> Dict[str, Any]:
        """
        Get org-wide worst sites for a SPECIFIC SLE metric.
        
        Uses the GET /api/v1/orgs/{org_id}/insights/worst-sites-by-sle endpoint
        with all_sle=false to return sites sorted by worst performance for a 
        specific metric only.
        
        Args:
            metric: Specific SLE metric name. Examples:
                WiFi: time-to-connect, successful-connect, coverage, roaming, throughput,
                      capacity, ap-health, ap-availability
                Wired: switch-health, switch-stc, switch-throughput, switch-stc-new
                WAN: gateway-health, wan-link-health
            duration: Time range for aggregation. Valid values: "1h", "3h", "6h", "12h", "1d", "7d"
            limit: Maximum number of sites to return (default: 100)
                
        Returns:
            Dict with keys:
                - success (bool): Whether API call succeeded
                - metric (str): The requested SLE metric
                - duration (str): The time range used
                - sites (list): List of site data sorted by worst performers for this metric
                - error (str): Error message if success is False
        """
        try:
            session = self._get_session()
            
            # Ensure we have an org_id
            if not self.org_id:
                test_result = self.test_connection()
                if not test_result["success"]:
                    return {
                        "success": False,
                        "metric": metric,
                        "duration": duration,
                        "sites": [],
                        "error": "Could not determine organization ID"
                    }
            
            org_id: str = self.org_id or ""
            
            logger.info(f"Fetching worst sites by metric '{metric}' (duration: {duration})")
            
            # Fetch all sites for name resolution (paginate to get all sites)
            site_name_map = {}
            page = 1
            while True:
                sites_response = mistapi.api.v1.orgs.sites.listOrgSites(
                    session, org_id, limit=1000, page=page
                )
                if sites_response and hasattr(sites_response, "data"):
                    sites_data = sites_response.data or []
                    if not sites_data:
                        break  # No more sites
                    for site in sites_data:
                        site_id = site.get("id", "")
                        site_name = site.get("name", "Unknown Site")
                        site_name_map[site_id] = site_name
                    # Check if there are more pages
                    if len(sites_data) < 1000:
                        break  # Last page
                    page += 1
                else:
                    break
            
            logger.debug(f"Built site name map with {len(site_name_map)} sites")
            
            # Calculate time range
            import time
            end_time = int(time.time())
            duration_seconds = {
                "1h": 3600,
                "3h": 10800,
                "6h": 21600,
                "12h": 43200,
                "1d": 86400,
                "7d": 604800
            }
            start_time = end_time - duration_seconds.get(duration, 86400)
            
            # Call the worst-sites-by-sle endpoint
            # API: GET /api/v1/orgs/{org_id}/insights/worst-sites-by-sle
            # The 'sle' param determines which metric to rank/sort by
            # all_sle=true (default) returns all SLE metrics for the category, which we need for display
            # API supports limit param (undocumented, default is 10)
            uri = f"/api/v1/orgs/{org_id}/insights/worst-sites-by-sle"
            # mist_get expects query as a separate dict with string values
            query_params = {
                "sle": metric,
                "start": str(start_time),
                "end": str(end_time),
                # all_sle defaults to true - includes all metrics for display
                "limit": str(limit)
            }
            
            response = session.mist_get(uri, query=query_params)
            
            sites_list = []
            if response and response.status_code == 200:
                data = response.data if hasattr(response, "data") else {}
                
                # API can return either {"results": [...]} or just a list
                if isinstance(data, list):
                    results = data
                elif isinstance(data, dict):
                    results = data.get("results", [])
                else:
                    results = []
                
                for site_data in results:
                    site_id = site_data.get("site_id", "")
                    site_name = site_name_map.get(site_id, "Unknown Site")
                    
                    site_entry = {
                        "site_id": site_id,
                        "site_name": site_name
                    }
                    
                    # Copy all data from API response
                    for key, value in site_data.items():
                        if key != "site_id":
                            site_entry[key] = value
                    
                    sites_list.append(site_entry)
            elif response:
                logger.warning(f"API returned status {response.status_code} for worst-sites-by-sle (metric: {metric})")
            
            # Apply client-side limit since API doesn't support limit parameter
            sites_limited = sites_list[:limit]
            
            logger.info(f"Retrieved {len(sites_list)} worst sites, returning top {len(sites_limited)} for metric '{metric}'")
            
            return {
                "success": True,
                "metric": metric,
                "duration": duration,
                "sites": sites_limited,
                "total_sites": len(sites_limited)
            }
            
        except Exception as error:
            logger.error(f"Error fetching worst sites for metric '{metric}': {error}")
            return {
                "success": False,
                "metric": metric,
                "duration": duration,
                "sites": [],
                "error": str(error)
            }
