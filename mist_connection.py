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
License: CC BY-NC-SA 4.0 (https://creativecommons.org/licenses/by-nc-sa/4.0/)

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
import inspect
import logging
import os
import time
from typing import Any

# Third-party imports
# Juniper Mist API Python SDK (tmunzer/mistapi_python)
import mistapi

from perf_monitor import (
    duration_ms_since,
    emit_perf_event,
    finish_peak_trace,
    install_requests_response_hook,
    instrument_mist_method,
    perf_enabled,
    stage,
    start_peak_trace,
)

# =============================================================================
# MODULE CONFIGURATION
# =============================================================================

# Create module-level logger
# Inherits configuration from app.py when used as a module
logger = logging.getLogger(__name__)

NestedRecord = dict[str, Any]
HealthData = dict[str, NestedRecord]


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
        cache_hit = self.session is not None
        trace_started = start_peak_trace()
        start = time.perf_counter()
        if self.session is None:
            self.session = mistapi.APISession(
                host=self.api_host, apitoken=self.api_token or ""
            )
        hook_installed = install_requests_response_hook(self.session)
        if perf_enabled():
            emit_perf_event(
                event_name="mist_session",
                module=__name__,
                function="_get_session",
                duration_ms=duration_ms_since(start),
                processing_ms=duration_ms_since(start),
                call_count=0,
                cache_hit=cache_hit,
                peak_bytes=finish_peak_trace(trace_started),
                hook_installed=hook_installed,
            )
        return self.session

    # =========================================================================
    # CONNECTION AND ORGANIZATION METHODS
    # =========================================================================

    def test_connection(self) -> dict[str, Any]:
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
                        "org_id": self.org_id,
                    }
                else:
                    return {
                        "success": True,
                        "org_name": "Unknown",
                        "org_id": self.org_id,
                    }
            else:
                # Auto-detect organization from API token privileges
                response = mistapi.api.v1.self.self.getSelf(session)
                self_data = response.data if hasattr(response, "data") else response
                privileges = (
                    self_data.get("privileges", [])
                    if isinstance(self_data, dict)
                    else []
                )

                if privileges:
                    # Use the first organization the token has access to
                    first_org = privileges[0]
                    self.org_id = first_org.get("org_id")
                    return {
                        "success": True,
                        "org_name": first_org.get("name", "Unknown"),
                        "org_id": self.org_id,
                    }
                else:
                    return {
                        "success": False,
                        "error": "No organizations found for this API token",
                    }

        except Exception as error:
            logger.error(f"Connection test failed: {error}")
            return {"success": False, "error": str(error)}

    # =========================================================================
    # SITE DATA METHODS
    # =========================================================================

    def get_sites(self) -> list[dict[str, Any]]:
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
                session, org_id, limit=1000  # Maximum items per page
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
                    "timezone": site.get("timezone", ""),
                }
                for site in sites
            ]

        except Exception as error:
            logger.error(f"Error fetching sites: {error}")
            raise

    def get_site_info(self, site_id: str) -> dict[str, Any]:
        """
        Get basic information for a single site.

        Args:
            site_id: UUID of the site to query

        Returns:
            dict: Site information including name, address, timezone
        """
        try:
            session = self._get_session()
            response = mistapi.api.v1.sites.sites.getSiteInfo(session, site_id)
            site_data = response.data if hasattr(response, "data") else response
            # Ensure we have a dict for type safety
            if not isinstance(site_data, dict):
                site_data = {}
            return {
                "id": site_data.get("id"),
                "name": site_data.get("name", "Unknown"),
                "address": site_data.get("address", ""),
                "country_code": site_data.get("country_code", ""),
                "timezone": site_data.get("timezone", ""),
            }
        except Exception as error:
            logger.error(f"Error fetching site info: {error}")
            raise

    # =========================================================================
    # DEVICE HEALTH METHODS
    # =========================================================================

    def get_site_health(self, site_id: str) -> dict[str, Any]:
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
            deviceprofile_ssids: dict[str, list[str]] = {}
            try:
                # Fetch org templates to build template -> device profile mapping
                with stage(
                    "template_fetch",
                    module=__name__,
                    function="get_site_health",
                    site_id=site_id,
                ):
                    templates_response = mistapi.api.v1.orgs.templates.listOrgTemplates(
                        session, org_id
                    )
                    templates_data = (
                        templates_response.data
                        if hasattr(templates_response, "data")
                        else templates_response
                    )
                    templates = (
                        templates_data if isinstance(templates_data, list) else []
                    )

                # Build template_id -> deviceprofile_ids mapping
                # Templates define which device profiles should receive which configs
                template_to_deviceprofiles = self._template_profiles(templates)

                # Fetch org WLANs to map SSIDs to device profiles
                with stage(
                    "wlan_mapping",
                    module=__name__,
                    function="get_site_health",
                    site_id=site_id,
                ):
                    wlans_response = mistapi.api.v1.orgs.wlans.listOrgWlans(
                        session, org_id
                    )
                    wlans_data = (
                        wlans_response.data
                        if hasattr(wlans_response, "data")
                        else wlans_response
                    )
                    org_wlans = wlans_data if isinstance(wlans_data, list) else []

                    # Build deviceprofile_id -> SSIDs mapping
                    self._map_profile_wlans(
                        org_wlans, template_to_deviceprofiles, deviceprofile_ssids
                    )

                    logger.debug(
                        f"Built SSID mapping for {len(deviceprofile_ssids)} device profiles"
                    )
            except Exception as mapping_error:
                # Non-fatal: continue without SSID mapping if it fails
                logger.warning(
                    f"Could not build device profile SSID mapping: {mapping_error}"
                )

            # -----------------------------------------------------------------
            # Fetch device stats for all device types
            # -----------------------------------------------------------------
            with stage(
                "device_stats_fetch",
                module=__name__,
                function="get_site_health",
                site_id=site_id,
            ):
                response = mistapi.api.v1.sites.stats.listSiteDevicesStats(
                    session,
                    site_id,
                    type="all",  # Fetch APs, switches, and gateways
                    limit=1000,
                )
                devices = mistapi.get_all(response=response, mist_session=session) or []

            # -----------------------------------------------------------------
            # Initialize health data structure
            # -----------------------------------------------------------------
            health_data: HealthData = {
                "aps": {"total": 0, "connected": 0, "disconnected": 0, "devices": []},
                "switches": {
                    "total": 0,
                    "connected": 0,
                    "disconnected": 0,
                    "devices": [],
                },
                "gateways": {
                    "total": 0,
                    "connected": 0,
                    "disconnected": 0,
                    "devices": [],
                },
                "summary": {
                    "total": 0,
                    "connected": 0,
                    "disconnected": 0,
                    "health_percentage": 0,
                },
            }

            # -----------------------------------------------------------------
            # Process each device and categorize by type
            # -----------------------------------------------------------------
            categorize_start = time.perf_counter()
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
                    "notes": device.get("notes", ""),
                }

                # Categorize by device type and add type-specific fields
                if device_type == "ap":
                    # ---------------------------------------------------------
                    # AP-specific fields
                    # ---------------------------------------------------------
                    self._add_ap_health(device, device_summary, deviceprofile_ssids)

                    # Update AP counters
                    self._increment_health(health_data["aps"], is_connected)
                    health_data["aps"]["devices"].append(device_summary)

                elif device_type == "switch":
                    # ---------------------------------------------------------
                    # Switch-specific fields
                    # ---------------------------------------------------------
                    # Client stats are nested under clients_stats.total
                    self._add_switch_health(device, device_summary)

                    # Update switch counters
                    self._increment_health(health_data["switches"], is_connected)
                    health_data["switches"]["devices"].append(device_summary)

                elif device_type == "gateway":
                    # ---------------------------------------------------------
                    # Gateway-specific fields (basic info only, WAN details separate)
                    # ---------------------------------------------------------
                    self._increment_health(health_data["gateways"], is_connected)
                    health_data["gateways"]["devices"].append(device_summary)

                # Unknown device types still contribute to the overall summary.
                self._increment_health(health_data["summary"], is_connected)
            emit_perf_event(
                event_name="mist_api_stage",
                module=__name__,
                function="get_site_health.device_categorization",
                site_id=site_id,
                duration_ms=duration_ms_since(categorize_start),
                items_returned=len(devices),
            )

            # -----------------------------------------------------------------
            # Calculate overall health percentage
            # -----------------------------------------------------------------
            with stage(
                "summary_build",
                module=__name__,
                function="get_site_health",
                site_id=site_id,
            ):
                if health_data["summary"]["total"] > 0:
                    health_data["summary"]["health_percentage"] = round(
                        (
                            health_data["summary"]["connected"]
                            / health_data["summary"]["total"]
                        )
                        * 100,
                        1,
                    )

            return health_data

        except Exception as error:
            logger.error(f"Error fetching site health for {site_id}: {error}")
            raise

    @staticmethod
    def _template_profiles(templates: list[Any]) -> dict[str, list[str]]:
        profiles = {}
        for template in templates:
            if not isinstance(template, dict):
                continue
            template_id = template.get("id", "")
            profile_ids = template.get("deviceprofile_ids", [])
            if (
                template_id
                and profile_ids
                and template.get("filter_by_deviceprofile", False)
            ):
                profiles[template_id] = profile_ids
        return profiles

    @staticmethod
    def _map_profile_wlans(
        wlans: list[Any],
        templates: dict[str, list[str]],
        profiles: dict[str, list[str]],
    ) -> None:
        for wlan in wlans:
            if not isinstance(wlan, dict) or not wlan.get("enabled", True):
                continue
            ssid = wlan.get("ssid", "")
            template_id = wlan.get("template_id", "")
            if not ssid or not template_id or template_id not in templates:
                continue
            for profile_id in templates[template_id]:
                ssids = profiles.setdefault(profile_id, [])
                if ssid not in ssids:
                    ssids.append(ssid)

    @staticmethod
    def _first_list_item(items: Any, default: Any) -> Any:
        return items[0] if isinstance(items, list) and items else default

    @staticmethod
    def _increment_health(counts: NestedRecord, connected: bool) -> None:
        counts["total"] += 1
        counts["connected" if connected else "disconnected"] += 1

    @staticmethod
    def _add_ap_health(
        device: NestedRecord, summary: NestedRecord, profiles: dict[str, list[str]]
    ) -> None:
        summary["num_clients"] = device.get("num_clients", 0) or 0
        summary["power_src"] = device.get("power_src", "")
        summary["power_opmode"] = device.get("power_opmode", "")
        summary["port_speed"] = (
            device.get("port_stat", {}).get("eth0", {}).get("speed", 0)
        )
        ssids = profiles.get(device.get("deviceprofile_id", ""), [])
        summary["ssids"] = ssids.copy() if ssids else []

    def _add_switch_health(self, device: NestedRecord, summary: NestedRecord) -> None:
        clients = device.get("clients_stats", {}).get("total", {})
        summary["num_wired_clients"] = clients.get("num_wired_clients", 0) or 0
        summary["num_wifi_clients"] = clients.get("num_wifi_clients", 0) or 0
        summary["num_aps"] = self._first_list_item(clients.get("num_aps", [0]), 0)

    # =========================================================================
    # SLE (SERVICE LEVEL EXPERIENCE) METHODS
    # =========================================================================

    def get_site_sle(self, site_id: str, duration: str = "1d") -> dict[str, Any]:
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
            use_timestamps = duration == "10m"

            start_time: int = 0
            end_time: int = 0
            api_duration: str = "1d"

            if use_timestamps:
                # Calculate start/end epoch timestamps for 10 minutes
                end_time = int(time.time())
                start_time = end_time - 600  # 10 minutes = 600 seconds
            else:
                # Map duration values to API-compatible formats
                duration_map = {"1h": "1h", "today": "1d", "1d": "1d", "1w": "1w"}
                api_duration = duration_map.get(duration, "1d")

            # -----------------------------------------------------------------
            # Initialize SLE data structure
            # -----------------------------------------------------------------
            sle_data: dict[str, NestedRecord] = {
                "wifi": {"metrics": {}, "available": False},
                "wired": {"metrics": {}, "available": False},
                "wan": {"metrics": {}, "available": False},
            }

            # Define which metrics belong to which category
            # These are the standard SLE metric names from the Mist API
            metric_categories = {
                "wifi": [
                    "coverage",
                    "capacity",
                    "time-to-connect",
                    "roaming",
                    "throughput",
                    "ap-availability",
                    "ap-health",
                ],
                "wired": ["switch-health-v2", "switch-throughput", "switch-stc"],
                "wan": [
                    "gateway-health",
                    "wan-link-health",
                    "application-health",
                    "gateway-bandwidth",
                ],
            }

            # -----------------------------------------------------------------
            # Get list of enabled metrics for the site
            # -----------------------------------------------------------------
            try:
                metrics_response = mistapi.api.v1.sites.sle.listSiteSlesMetrics(
                    session, site_id, scope="site", scope_id=site_id
                )
                metrics_data = (
                    metrics_response.data
                    if hasattr(metrics_response, "data")
                    else metrics_response
                )
                enabled_metrics = (
                    metrics_data.get("enabled", [])
                    if isinstance(metrics_data, dict)
                    else []
                )
            except Exception as e:
                logger.debug(f"Could not get enabled metrics for site {site_id}: {e}")
                enabled_metrics = []

            # -----------------------------------------------------------------
            # Fetch summary for each enabled metric and categorize
            # -----------------------------------------------------------------
            for metric in enabled_metrics:
                # Determine which category this metric belongs to
                category = self._metric_category(metric, metric_categories)

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
                        summary_response = (
                            mistapi.api.v1.sites.sle.getSiteSleSummaryTrend(
                                session,
                                site_id,
                                scope="site",
                                scope_id=site_id,
                                metric=metric,
                                start=str(start_time),
                                end=str(end_time),
                            )
                        )
                    else:
                        # Use duration parameter for standard time ranges
                        summary_response = (
                            mistapi.api.v1.sites.sle.getSiteSleSummaryTrend(
                                session,
                                site_id,
                                scope="site",
                                scope_id=site_id,
                                metric=metric,
                                duration=api_duration,
                            )
                        )
                    summary_data = (
                        summary_response.data
                        if hasattr(summary_response, "data")
                        else summary_response
                    )

                    # ---------------------------------------------------------
                    # Calculate SLE percentage from sample data
                    # ---------------------------------------------------------
                    # Formula: SLE % = ((total - degraded) / total) * 100
                    # The API returns arrays of sample values that need to be summed
                    sle_value = self._sle_value(summary_data)
                    if sle_value is not None:
                        self._record_sle_value(sle_data[category], metric, sle_value)

                except Exception as metric_error:
                    logger.debug(
                        f"Could not fetch {metric} for site {site_id}: {metric_error}"
                    )

            return sle_data

        except Exception as error:
            logger.error(f"Error fetching site SLE for {site_id}: {error}")
            raise

    @staticmethod
    def _sum_samples(samples: list[Any]) -> float:
        return sum(value for value in samples if value is not None)

    def _sle_value(self, data: Any) -> float | None:
        if not isinstance(data, dict) or "sle" not in data:
            return None
        samples = data.get("sle", {}).get("samples", {})
        total = self._sum_samples(samples.get("total", []))
        degraded = self._sum_samples(samples.get("degraded", []))
        if total > 0:
            return round(((total - degraded) / total) * 100, 1)
        return None

    @staticmethod
    def _record_sle_value(category: NestedRecord, metric: str, value: float) -> None:
        category["available"] = True
        name = metric.replace("-v2", "").replace("-v4", "").replace("-new", "")
        if name not in category["metrics"]:
            category["metrics"][name] = value

    @staticmethod
    def _metric_category(metric: str, categories: dict[str, list[str]]) -> str | None:
        for category, metrics in categories.items():
            if metric in metrics or any(metric.startswith(name) for name in metrics):
                return category
        return None

    def get_sle_classifiers(
        self, site_id: str, metric: str, scope: str = "site"
    ) -> list[dict[str, Any]]:
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
                session, site_id, scope=scope, scope_id=site_id, metric=metric
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
        scope: str = "site",
    ) -> dict[str, Any]:
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
                duration=duration,
            )
            data = response.data if hasattr(response, "data") else response

            return data if isinstance(data, dict) else {}

        except Exception as error:
            logger.error(
                f"Error fetching classifier details for {metric}/{classifier}: {error}"
            )
            return {}

    def get_sle_impact_summary(
        self,
        site_id: str,
        metric: str,
        duration: str = "1d",
        classifier: str | None = None,
        scope: str = "site",
    ) -> dict[str, Any]:
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
            kwargs: dict[str, Any] = {
                "mist_session": session,
                "site_id": site_id,
                "scope": scope,
                "scope_id": site_id,
                "metric": metric,
                "duration": duration,
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
        classifier: str | None = None,
    ) -> dict[str, Any]:
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

            # Determine scope based on metric type
            # WAN metrics (gateway-*) use 'site' scope with site_id
            # Switch metrics (switch-*) would use 'switch' scope
            # WiFi metrics use 'site' scope
            scope = "site"
            scope_id = site_id

            # Build kwargs for API call
            base_kwargs: dict[str, Any] = {
                "mist_session": session,
                "site_id": site_id,
                "scope": scope,
                "scope_id": scope_id,
                "metric": metric,
                "duration": duration,
            }
            if classifier:
                base_kwargs["classifier"] = classifier

            # Map item_type to appropriate API function and response key
            api_map = {
                "gateways": (
                    mistapi.api.v1.sites.sle.listSiteSleImpactedGateways,
                    "gateways",
                ),
                "interfaces": (
                    mistapi.api.v1.sites.sle.listSiteSleImpactedInterfaces,
                    "interfaces",
                ),
                "applications": (
                    mistapi.api.v1.sites.sle.listSiteSleImpactedApplications,
                    "apps",
                ),
                "clients": (
                    mistapi.api.v1.sites.sle.listSiteSleImpactedWiredClients,
                    "clients",
                ),
                "wireless_clients": (
                    mistapi.api.v1.sites.sle.listSiteSleImpactedWirelessClients,
                    "users",
                ),
            }

            if item_type not in api_map:
                return {
                    "total_count": 0,
                    "items": [],
                    "error": f"Invalid item_type: {item_type}",
                }

            api_func, response_key = api_map[item_type]
            response = api_func(**base_kwargs)
            data = response.data if hasattr(response, "data") else response

            if not isinstance(data, dict):
                return {"total_count": 0, "items": []}

            raw_items = data.get(response_key, [])
            total_count = data.get("total_count", len(raw_items))

            # Debug: Log raw item fields to understand API response format
            if raw_items:
                logger.info(
                    f"WAN Impact Debug [{item_type}]: First raw item keys: {list(raw_items[0].keys())}"
                )
                logger.info(
                    f"WAN Impact Debug [{item_type}]: First raw item: {raw_items[0]}"
                )

            # Calculate failure rate and overall impact for each item
            total_degraded_all = sum(item.get("degraded", 0) for item in raw_items)

            items = []
            for item in raw_items:
                degraded = item.get("degraded", 0)
                total = item.get("total", 0)
                failure_rate = round((degraded / total) * 100, 1) if total > 0 else 0
                overall_impact = (
                    round((degraded / total_degraded_all) * 100, 1)
                    if total_degraded_all > 0
                    else 0
                )

                processed_item = {
                    **item,
                    "failure_rate": failure_rate,
                    "overall_impact": overall_impact,
                }
                items.append(processed_item)

            # Sort by overall_impact descending (most impactful first)
            items.sort(key=lambda x: x.get("overall_impact", 0), reverse=True)

            return {
                "total_count": total_count,
                "metric": metric,
                "classifier": classifier or "",
                "items": items,
            }

        except Exception as error:
            logger.error(f"Error fetching impacted {item_type} for {metric}: {error}")
            return {"total_count": 0, "items": [], "error": str(error)}

    def get_sle_details(
        self, site_id: str, category: str, duration: str = "1d"
    ) -> dict[str, Any]:
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
                "wifi": [
                    "coverage",
                    "capacity",
                    "time-to-connect",
                    "roaming",
                    "throughput",
                    "ap-availability",
                    "ap-health",
                ],
                "wired": ["switch-health-v2", "switch-throughput", "switch-stc"],
                "wan": [
                    "gateway-health",
                    "wan-link-health",
                    "application-health",
                    "gateway-bandwidth",
                ],
            }

            if category not in metric_categories:
                raise ValueError(f"Invalid category: {category}")

            # -----------------------------------------------------------------
            # Get list of enabled metrics for the site
            # -----------------------------------------------------------------
            try:
                metrics_response = mistapi.api.v1.sites.sle.listSiteSlesMetrics(
                    session, site_id, scope="site", scope_id=site_id
                )
                metrics_data = (
                    metrics_response.data
                    if hasattr(metrics_response, "data")
                    else metrics_response
                )
                enabled_metrics = (
                    metrics_data.get("enabled", [])
                    if isinstance(metrics_data, dict)
                    else []
                )
            except Exception as e:
                logger.debug(f"Could not get enabled metrics: {e}")
                enabled_metrics = []

            # -----------------------------------------------------------------
            # Filter to only metrics in this category that are enabled
            # -----------------------------------------------------------------
            category_metrics = self._enabled_category_metrics(
                metric_categories[category], enabled_metrics
            )

            result: dict[str, Any] = {
                "category": category,
                "duration": duration,
                "metrics": {},
            }

            for metric in category_metrics:
                # Find actual metric name (might have version suffix like -v2)
                actual_metric = self._actual_metric(metric, enabled_metrics)

                metric_data: dict[str, Any] = {
                    "name": metric,
                    "sle_value": None,
                    "classifiers": [],
                    "impact": {},
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
                        duration=duration,
                    )
                    summary_data = (
                        summary_response.data
                        if hasattr(summary_response, "data")
                        else summary_response
                    )

                    # Also get the deprecated summary to extract impact data per classifier
                    # The trend API doesn't include per-classifier impact, only the deprecated one does
                    classifier_impact_map: dict[str, dict[str, Any]] = {}
                    try:
                        impact_response = mistapi.api.v1.sites.sle.getSiteSleSummary(
                            session,
                            site_id,
                            scope="site",
                            scope_id=site_id,
                            metric=actual_metric,
                            duration=duration,
                        )
                        impact_data = (
                            impact_response.data
                            if hasattr(impact_response, "data")
                            else impact_response
                        )
                        classifier_impact_map = self._classifier_impact_map(impact_data)
                    except Exception as e:
                        logger.debug(
                            f"Could not get impact data from deprecated API for {actual_metric}: {e}"
                        )

                    if isinstance(summary_data, dict):
                        # Extract overall SLE value
                        metric_data["sle_value"] = self._sle_value(summary_data)

                        # Extract overall impact
                        metric_data["impact"] = summary_data.get("impact", {})

                        # Extract classifiers from summary (already contains full data)
                        raw_classifiers = summary_data.get("classifiers", [])

                        # Calculate total degraded across all classifiers for percentage calc
                        total_classifier_degraded = self._total_classifier_degraded(
                            raw_classifiers
                        )

                        # Process each classifier
                        for clf in raw_classifiers:
                            if isinstance(clf, dict):
                                self._append_sle_classifier(
                                    clf,
                                    classifier_impact_map,
                                    total_classifier_degraded,
                                    metric_data["classifiers"],
                                )

                        # Sort classifiers by percentage (highest first)
                        metric_data["classifiers"].sort(
                            key=lambda x: x.get("percentage", 0), reverse=True
                        )

                except Exception as e:
                    logger.debug(f"Could not get summary for {actual_metric}: {e}")

                result["metrics"][metric] = metric_data

            return result

        except Exception as error:
            logger.error(f"Error fetching SLE details for {category}: {error}")
            raise

    @staticmethod
    def _classifier_impact_map(data: Any) -> dict[str, NestedRecord]:
        impacts = {}
        if isinstance(data, dict):
            for classifier in data.get("classifiers", []):
                if (
                    isinstance(classifier, dict)
                    and "name" in classifier
                    and "impact" in classifier
                ):
                    impacts[classifier["name"]] = classifier["impact"]
        return impacts

    @staticmethod
    def _enabled_category_metrics(metrics: list[str], enabled: list[str]) -> list[str]:
        return [
            metric
            for metric in metrics
            if metric in enabled or any(name.startswith(metric) for name in enabled)
        ]

    @staticmethod
    def _actual_metric(metric: str, enabled: list[str]) -> str:
        for name in enabled:
            if name.startswith(metric):
                return name
        return metric

    def _total_classifier_degraded(self, classifiers: list[Any]) -> float:
        total: float = 0
        for classifier in classifiers:
            if isinstance(classifier, dict):
                total += self._sum_samples(
                    classifier.get("samples", {}).get("degraded", [])
                )
        return total

    @staticmethod
    def _normalized_classifier_impact(impact: NestedRecord) -> NestedRecord:
        keys = (
            "num_aps",
            "total_aps",
            "num_gateways",
            "total_gateways",
            "num_switches",
            "total_switches",
            "num_users",
            "total_users",
        )
        return {key: impact.get(key, 0) or 0 for key in keys}

    def _append_sle_classifier(
        self,
        classifier: NestedRecord,
        impacts: dict[str, NestedRecord],
        total: float,
        classifiers: list[NestedRecord],
    ) -> None:
        name = classifier.get("name", "unknown")
        samples = classifier.get("samples", {})
        degraded = self._sum_samples(samples.get("degraded", []))
        percentage = round((degraded / total) * 100, 1) if total > 0 else 0
        info = {
            "name": name,
            "degraded_sum": degraded,
            "percentage": percentage,
            "impact": self._normalized_classifier_impact(impacts.get(name, {})),
            "samples": samples,
        }
        if degraded > 0:
            classifiers.append(info)

    def get_classifier_impact_details(
        self, site_id: str, metric: str, classifier: str, duration: str = "1d"
    ) -> dict[str, Any]:
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

            # For wired metrics, we need to request switch,chassis fields
            # For WiFi metrics, we need ap,wlan,device_type,device_os,band fields
            is_wired_metric = metric.startswith("switch-")

            # Fetch impact summary from the Mist API
            if is_wired_metric:
                # Wired metrics need fields=switch,chassis
                response = mistapi.api.v1.sites.sle.getSiteSleImpactSummary(
                    session,
                    site_id,
                    scope="site",
                    scope_id=site_id,
                    metric=metric,
                    classifier=classifier,
                    duration=duration,
                    fields="switch,chassis",
                )
            else:
                # WiFi and WAN metrics
                response = mistapi.api.v1.sites.sle.getSiteSleImpactSummary(
                    session,
                    site_id,
                    scope="site",
                    scope_id=site_id,
                    metric=metric,
                    classifier=classifier,
                    duration=duration,
                )

            data = response.data if hasattr(response, "data") else response

            # Log raw data for debugging
            logger.debug(
                f"Impact summary raw data keys: {list(data.keys()) if isinstance(data, dict) else type(data)}"
            )

            # Return empty structure if no valid data
            if not isinstance(data, dict):
                return {
                    "aps": [],
                    "wlans": [],
                    "device_types": [],
                    "device_os": [],
                    "bands": [],
                    "switches": [],
                    "chassis": [],
                }

            # -----------------------------------------------------------------
            # Process APs - extract only those with actual degradation
            # -----------------------------------------------------------------
            aps = self._client_impact_records(data, "ap")

            # -----------------------------------------------------------------
            # Process WLANs - extract only those with actual degradation
            # -----------------------------------------------------------------
            wlans = self._client_impact_records(data, "wlan")

            # -----------------------------------------------------------------
            # Process device types (laptop, phone, tablet, etc.)
            # -----------------------------------------------------------------
            device_types = self._client_impact_records(data, "device_type")

            # -----------------------------------------------------------------
            # Process device operating systems
            # -----------------------------------------------------------------
            device_os = self._client_impact_records(data, "device_os")

            # -----------------------------------------------------------------
            # Process WiFi bands with human-readable names
            # -----------------------------------------------------------------
            bands = []
            for band in data.get("band", []):
                if band.get("degraded", 0) > 0:
                    # Convert band identifiers to human-readable format
                    band_name = band.get("band", band.get("name", "Unknown"))
                    band_name = self._band_name(band_name)
                    bands.append(
                        {
                            "band": band_name,
                            "degraded": band.get("degraded", 0),
                            "total": band.get("total", 0),
                        }
                    )
            bands.sort(key=lambda x: x["degraded"], reverse=True)

            # -----------------------------------------------------------------
            # Process Switches (for wired SLEs)
            # Response format: {"switch_mac": "...", "name": "...", "degraded": 1.95,
            #                   "total": 1251.53, "duration": 1251.53, "switch_model": "...", "switch_version": "..."}
            # -----------------------------------------------------------------
            switches = []
            for sw in data.get("switch", []):
                if sw.get("degraded", 0) > 0 or sw.get("duration", 0) > 0:
                    switches.append(
                        {
                            "mac": sw.get("switch_mac", sw.get("mac", "")),
                            "name": sw.get("name", sw.get("switch_mac", "Unknown")),
                            "degraded": sw.get("degraded", 0),
                            "total": sw.get("total", 0),
                            "duration": sw.get("duration", 0),
                            "model": sw.get("switch_model", ""),
                            "version": sw.get("switch_version", ""),
                            "chassis_mac": sw.get("chassis_mac", ""),
                        }
                    )
            switches.sort(key=lambda x: x.get("degraded", 0), reverse=True)

            # -----------------------------------------------------------------
            # Process Chassis (for wired SLEs with virtual chassis)
            # Response format: {"chassis": "0", "switch_mac": "...", "switch_name": "...",
            #                   "degraded": 1.95, "total": 1.95, "role": "master"}
            # -----------------------------------------------------------------
            chassis = []
            for ch in data.get("chassis", []):
                if ch.get("degraded", 0) > 0 or ch.get("duration", 0) > 0:
                    chassis.append(
                        {
                            "chassis_id": ch.get("chassis", ""),
                            "switch_mac": ch.get("switch_mac", ""),
                            "switch_name": ch.get("switch_name", ""),
                            "degraded": ch.get("degraded", 0),
                            "total": ch.get("total", 0),
                            "duration": ch.get("duration", 0),
                            "role": ch.get("role", ""),
                            "chassis_mac": ch.get("chassis_mac", ""),
                        }
                    )
            chassis.sort(key=lambda x: x.get("degraded", 0), reverse=True)

            # Log raw data keys for debugging wired SLEs
            if metric.startswith("switch-"):
                self._log_wired_impact(data)

            return {
                "metric": metric,
                "classifier": classifier,
                "aps": aps,
                "wlans": wlans,
                "device_types": device_types,
                "device_os": device_os,
                "bands": bands,
                "switches": switches,
                "chassis": chassis,
            }

        except Exception as error:
            logger.error(f"Error fetching classifier impact details: {error}")
            return {
                "aps": [],
                "wlans": [],
                "device_types": [],
                "device_os": [],
                "bands": [],
                "switches": [],
                "chassis": [],
            }

    # =========================================================================
    # DEVICE AND CLIENT METHODS
    # =========================================================================

    def _client_impact_records(
        self, data: NestedRecord, kind: str
    ) -> list[NestedRecord]:
        records = []
        for item in data.get(kind, []):
            if item.get("degraded", 0) > 0:
                record = self._client_impact_identity(item, kind)
                record.update(
                    degraded=item.get("degraded", 0), total=item.get("total", 0)
                )
                records.append(record)
        records.sort(key=lambda item: item["degraded"], reverse=True)
        return records

    @staticmethod
    def _client_impact_identity(item: NestedRecord, kind: str) -> NestedRecord:
        if kind == "ap":
            return {
                "mac": item.get("ap_mac", ""),
                "name": item.get("name", item.get("ap_mac", "Unknown")),
            }
        if kind == "wlan":
            return {"id": item.get("wlan_id", ""), "name": item.get("name", "Unknown")}
        output_key = {"device_type": "type", "device_os": "os"}[kind]
        return {
            output_key: item.get(kind, item.get("name", "Unknown")),
            "name": item.get("name", "Unknown"),
        }

    @staticmethod
    def _band_name(name: Any) -> Any:
        # Do not coerce numeric or unknown identifiers: preserve the API value.
        if name == "24":
            return "2.4 GHz"
        if name == "5":
            return "5 GHz"
        if name == "6":
            return "6 GHz"
        return name

    @staticmethod
    def _log_wired_impact(data: NestedRecord) -> None:
        logger.info(f"Wired impact data keys: {list(data.keys())}")
        if data.get("switch"):
            logger.info(f"Found {len(data['switch'])} switches in impact data")
        if data.get("chassis"):
            logger.info(f"Found {len(data['chassis'])} chassis in impact data")

    def get_site_devices(
        self, site_id: str, device_type: str = "all"
    ) -> list[dict[str, Any]]:
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
                session, site_id, type=device_type, limit=1000
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
                    "mem_used_kb": device.get("mem_used_kb", 0),
                }
                for device in devices
            ]

        except Exception as error:
            logger.error(f"Error fetching devices for site {site_id}: {error}")
            raise

    def get_wireless_client_sessions(self, site_id: str) -> list[dict[str, Any]]:
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
            clients_by_mac: dict[str, NestedRecord] = {}

            # -----------------------------------------------------------------
            # Source 1: Real-time connected clients with detailed stats
            # -----------------------------------------------------------------
            try:
                stats_response = (
                    mistapi.api.v1.sites.stats.listSiteWirelessClientsStats(
                        session, site_id
                    )
                )
                stats_results = (
                    mistapi.get_all(response=stats_response, mist_session=session) or []
                )

                self._store_wireless_stats(clients_by_mac, stats_results)
            except Exception as e:
                logger.debug(f"Could not fetch wireless client stats: {e}")

            # -----------------------------------------------------------------
            # Source 2: Client search data (hostname, username, OS, etc.)
            # -----------------------------------------------------------------
            try:
                search_response = (
                    mistapi.api.v1.sites.clients.searchSiteWirelessClients(
                        session, site_id, limit=1000
                    )
                )
                search_results = (
                    mistapi.get_all(response=search_response, mist_session=session)
                    or []
                )

                for client in search_results:
                    mac = client.get("mac", "")
                    if mac:
                        if mac in clients_by_mac:
                            # Merge with existing data - prefer non-empty values
                            existing = clients_by_mac[mac]
                            self._merge_wireless_search(existing, client)
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
                                "is_connected": False,
                            }
            except Exception as e:
                logger.debug(f"Could not fetch wireless client search: {e}")

            # -----------------------------------------------------------------
            # Source 3: Session history (connect/disconnect times, duration)
            # -----------------------------------------------------------------
            try:
                sessions_response = (
                    mistapi.api.v1.sites.clients.searchSiteWirelessClientSessions(
                        session,
                        site_id,
                        duration="7d",  # Look back 7 days for historical sessions
                        limit=1000,
                    )
                )
                sessions_results = (
                    mistapi.get_all(response=sessions_response, mist_session=session)
                    or []
                )

                for sess in sessions_results:
                    mac = sess.get("mac", "")
                    if mac:
                        if mac in clients_by_mac:
                            # Update with session data if more recent or has more info
                            existing = clients_by_mac[mac]
                            self._merge_wireless_session(existing, sess)
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
                                "is_connected": False,
                            }
            except Exception as e:
                logger.debug(f"Could not fetch wireless client sessions: {e}")

            return list(clients_by_mac.values())

        except Exception as error:
            logger.error(f"Error fetching wireless clients for site {site_id}: {error}")
            raise

    @staticmethod
    def _merge_wireless_search(existing: NestedRecord, client: NestedRecord) -> None:
        for field in ("hostname", "ip", "username", "os", "ssid"):
            existing[field] = existing.get(field) or client.get(f"last_{field}", "")

    @staticmethod
    def _merge_wireless_session(existing: NestedRecord, session: NestedRecord) -> None:
        disconnect = session.get("disconnect", 0)
        if disconnect > existing.get("last_seen", 0):
            existing["last_seen"] = disconnect
        existing["connect"] = existing.get("connect") or session.get("connect", 0)
        existing["disconnect"] = disconnect
        existing["duration"] = session.get("duration", 0)
        existing["ssid"] = existing.get("ssid") or session.get("ssid", "")
        existing["manufacture"] = existing.get("manufacture") or session.get(
            "client_manufacture", ""
        )

    @staticmethod
    def _store_wireless_stats(
        clients: dict[str, NestedRecord], records: list[NestedRecord]
    ) -> None:
        for client in records:
            mac = client.get("mac", "")
            if mac:
                clients[mac] = {
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
                    "is_connected": True,
                }

    def get_wired_clients(self, site_id: str) -> list[dict[str, Any]]:
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
                    limit=1000,
                )
                all_results = (
                    mistapi.get_all(response=response, mist_session=session) or []
                )

                for client in all_results:
                    mac = client.get("mac", "")
                    if mac:
                        # Extract port info from device_mac_port array
                        port_info = self._wired_port_info(client)

                        # ---------------------------------------------------------
                        # Extract best available IP address
                        # ---------------------------------------------------------
                        ip = self._wired_ip(client.get("ip", []), port_info)

                        # ---------------------------------------------------------
                        # Parse timestamps for connection timing
                        # ---------------------------------------------------------
                        timestamp = self._wired_timestamp(client.get("timestamp", 0))
                        port_start = self._wired_timestamp(port_info.get("start", 0))

                        # ---------------------------------------------------------
                        # Determine connection status
                        # ---------------------------------------------------------
                        # Consider connected if seen within last 5 minutes
                        last_seen = timestamp if timestamp else port_start
                        is_connected = (
                            (current_time - last_seen) < 300 if last_seen else False
                        )

                        # Use port_start as connected_time if available
                        connected_time = (
                            port_start if port_start > 0 else (max(0, timestamp))
                        )

                        clients_by_mac[mac] = {
                            "mac": mac,
                            "hostname": client.get("dhcp_hostname", "")
                            or client.get("dhcp_fqdn", ""),
                            "ip": ip,
                            "username": client.get("username", ""),
                            "connected_time": connected_time,
                            "last_seen": last_seen,
                            "device_type": client.get(
                                "dhcp_vendor_class_identifier", ""
                            )
                            or client.get("dhcp_fingerprint", ""),
                            "is_connected": is_connected,
                            "switch_mac": port_info.get("device_mac", "")
                            or (
                                client.get("device_mac", [""])[0]
                                if client.get("device_mac")
                                else ""
                            ),
                            "port_id": port_info.get("port_id", ""),
                        }
            except Exception as e:
                logger.debug(f"Could not fetch wired clients: {e}")

            return list(clients_by_mac.values())

        except Exception as error:
            logger.error(f"Error fetching wired clients for site {site_id}: {error}")
            raise

    def _wired_port_info(self, client: NestedRecord) -> NestedRecord:
        port = self._first_list_item(client.get("device_mac_port", []), {})
        return port if isinstance(port, dict) else {}

    @staticmethod
    def _wired_ip(addresses: Any, port: NestedRecord) -> Any:
        if isinstance(addresses, list) and addresses:
            return addresses[0]
        if isinstance(addresses, str):
            return addresses
        if port.get("ip"):
            return port.get("ip", "")
        return ""

    @staticmethod
    def _wired_timestamp(value: Any) -> Any:
        if isinstance(value, str):
            try:
                return int(float(value)) if value else 0
            except (ValueError, TypeError):
                return 0
        return value

    def get_gateway_wan_status(self, site_id: str) -> list[dict[str, Any]]:
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
                session, site_id, type="gateway", limit=100
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
                    "bgp_peers": [],
                }

                # -------------------------------------------------------------
                # Extract WAN port information from if_stat
                # -------------------------------------------------------------
                # Only include ports where port_usage == "wan" or wan_type is set
                if_stat = gw.get("if_stat", {})

                gw_info["wan_ports"] = self._gateway_wan_ports(if_stat)

                # -------------------------------------------------------------
                # Fetch VPN peers for this gateway
                # -------------------------------------------------------------
                try:
                    vpn_response = mistapi.api.v1.orgs.stats.searchOrgPeerPathStats(
                        session, org_id, site_id=site_id, mac=gw_mac, limit=100
                    )
                    for vpn in self._gateway_peer_results(vpn_response):
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
                            "hop_count": vpn.get("hop_count", 0),
                        }
                        gw_info["vpn_peers"].append(vpn_peer)
                except Exception as e:
                    logger.debug(f"Could not fetch VPN peers for gateway {gw_mac}: {e}")

                # -------------------------------------------------------------
                # Fetch BGP peers for this gateway
                # -------------------------------------------------------------
                try:
                    bgp_response = mistapi.api.v1.orgs.stats.searchOrgBgpStats(
                        session, org_id, site_id=site_id, mac=gw_mac, limit=100
                    )
                    for bgp in self._gateway_peer_results(bgp_response):
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
                            "for_overlay": bgp.get("for_overlay", False),
                        }
                        gw_info["bgp_peers"].append(bgp_peer)
                except Exception as e:
                    logger.debug(f"Could not fetch BGP peers for gateway {gw_mac}: {e}")

                gateway_list.append(gw_info)

            return gateway_list

        except Exception as error:
            logger.error(
                f"Error fetching gateway WAN status for site {site_id}: {error}"
            )
            raise

    def _gateway_wan_ports(self, interfaces: NestedRecord) -> list[NestedRecord]:
        ports = []
        for name, stats in interfaces.items():
            if isinstance(stats, dict) and (
                stats.get("port_usage", "") == "wan" or stats.get("wan_type", "")
            ):
                ports.append(self._gateway_wan_port(name, stats))
        return ports

    def _gateway_wan_port(self, name: str, stats: NestedRecord) -> NestedRecord:
        return {
            "name": name,
            "wan_name": stats.get("wan_name", name),
            "status": "up" if stats.get("up", False) else "down",
            "ip": self._first_list_item(stats.get("ips", []), ""),
            "wan_type": stats.get("wan_type", "") or "ethernet",
            "address_mode": stats.get("address_mode", ""),
            "vlan": stats.get("vlan", 0),
            "port_id": stats.get("port_id", ""),
            "rx_bytes": stats.get("rx_bytes", 0),
            "tx_bytes": stats.get("tx_bytes", 0),
            "rx_pkts": stats.get("rx_pkts", 0),
            "tx_pkts": stats.get("tx_pkts", 0),
        }

    @staticmethod
    def _gateway_peer_results(response: Any) -> list[NestedRecord]:
        if response and hasattr(response, "data"):
            data = response.data
            return data.get("results", []) if isinstance(data, dict) else []
        return []

    def get_org_sle_insights(
        self, sle_type: str, duration: str = "1d", limit: int = 100
    ) -> dict[str, Any]:
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
                "error": f"Invalid sle_type '{sle_type}'. Must be one of: {valid_types}",
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
                        "error": "Could not determine organization ID",
                    }

            # org_id is guaranteed to be set after test_connection succeeds
            org_id: str = self.org_id or ""

            logger.info(
                f"Fetching org SLE insights for type '{sle_type}' (duration: {duration})"
            )

            # Fetch all sites for name resolution (paginate to get all sites)
            site_name_map = self._org_site_names(session, org_id)

            logger.debug(f"Built site name map with {len(site_name_map)} sites")

            # Use worst-sites-by-sle endpoint which returns sites sorted by worst performers
            # API: GET /api/v1/orgs/{org_id}/insights/worst-sites-by-sle
            # The sle parameter accepts category names: "wireless", "wired", "wan"
            # all_sle=true (default) returns all metrics in the category
            import time

            end_time = int(time.time())
            duration_seconds = {"1d": 86400, "7d": 604800, "2w": 1209600}
            start_time = end_time - duration_seconds.get(duration, 86400)

            # Map frontend category names to representative metrics
            # API doesn't accept category names like "wireless" - must use actual metrics
            # Using all_sle=true (default) returns all metrics in the same category
            sle_metric_map = {
                "wifi": "ap-availability",  # Returns all WiFi metrics
                "wired": "switch-stc",  # Returns all wired metrics (switch-health may return 0 results)
                "wan": "gateway-health",  # Returns all WAN metrics
            }
            sle_metric = sle_metric_map.get(sle_type, "ap-availability")

            # API supports limit param (undocumented, default is 10)
            uri = f"/api/v1/orgs/{org_id}/insights/worst-sites-by-sle"
            # mist_get expects query as a separate dict with string values
            query_params = {
                "sle": sle_metric,
                "start": str(start_time),
                "end": str(end_time),
                "limit": str(limit),
            }

            # Retry logic for intermittent 400 errors from Mist API
            response = self._get_worst_sites_response(session, uri, query_params)

            sites_list = []
            if response and response.status_code == 200:
                data = response.data if hasattr(response, "data") else {}
                results = data.get("results", []) if isinstance(data, dict) else data

                for site_data in results:
                    site_id = site_data.get("site_id", "")
                    site_name = site_name_map.get(site_id, "Unknown Site")

                    # Build site entry with all available SLE metrics
                    site_entry = {"site_id": site_id, "site_name": site_name}

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
                logger.warning(
                    f"API returned status {response.status_code} for worst-sites-by-sle"
                )

            # Apply client-side limit since API doesn't support limit parameter
            sites_limited = sites_list[:limit]

            logger.info(
                f"Retrieved {len(sites_list)} worst sites, returning top {len(sites_limited)} for category '{sle_type}'"
            )

            return {
                "success": True,
                "sle_type": sle_type,
                "duration": duration,
                "sites": sites_limited,
                "total_sites": len(sites_limited),
            }

        except Exception as error:
            logger.error(
                f"Error fetching org SLE insights for type '{sle_type}': {error}"
            )
            return {
                "success": False,
                "sle_type": sle_type,
                "duration": duration,
                "sites": [],
                "error": str(error),
            }

    def get_org_worst_sites_by_metric(
        self, metric: str, duration: str = "1d", limit: int = 100
    ) -> dict[str, Any]:
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
                        "error": "Could not determine organization ID",
                    }

            org_id: str = self.org_id or ""

            logger.info(
                f"Fetching worst sites by metric '{metric}' (duration: {duration})"
            )

            # Map metric to SLE category - API only accepts wifi/wired/wan for 'sle' parameter
            metric_to_category = {
                # WiFi metrics
                "time-to-connect": "wifi",
                "successful-connect": "wifi",
                "coverage": "wifi",
                "roaming": "wifi",
                "throughput": "wifi",
                "capacity": "wifi",
                "ap-health": "wifi",
                "ap-availability": "wifi",
                # Wired metrics
                "switch-health-v2": "wired",
                "switch-stc": "wired",
                "switch-throughput": "wired",
                "switch-bandwidth": "wired",
                # WAN metrics
                "gateway-health": "wan",
                "wan-link-health": "wan",
                "application-health": "wan",
                "gateway-bandwidth": "wan",
            }

            sle_category = metric_to_category.get(metric)
            if not sle_category:
                logger.warning(
                    f"Unknown metric '{metric}', defaulting to wifi category"
                )
                sle_category = "wifi"

            # Fetch all sites for name resolution (paginate to get all sites)
            site_name_map = self._org_site_names(session, org_id)

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
                "7d": 604800,
            }
            start_time = end_time - duration_seconds.get(duration, 86400)

            # Call the worst-sites-by-sle endpoint
            # API: GET /api/v1/orgs/{org_id}/insights/worst-sites-by-sle
            # The 'sle' param accepts the specific metric name (e.g., time-to-connect)
            uri = f"/api/v1/orgs/{org_id}/insights/worst-sites-by-sle"
            # mist_get expects query as a separate dict with string values
            query_params = {
                "sle": metric,
                "start": str(start_time),
                "end": str(end_time),
                "limit": str(limit),
            }

            # Retry logic for intermittent 400 errors from Mist API
            response = self._get_worst_sites_response(session, uri, query_params)

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

                    site_entry = {"site_id": site_id, "site_name": site_name}

                    # Copy all data from API response
                    for key, value in site_data.items():
                        if key != "site_id":
                            site_entry[key] = value

                    sites_list.append(site_entry)
            elif response:
                logger.warning(
                    f"API returned status {response.status_code} for worst-sites-by-sle (metric: {metric})"
                )

            # Apply client-side limit since API doesn't support limit parameter
            sites_limited = sites_list[:limit]

            logger.info(
                f"Retrieved {len(sites_list)} worst sites, returning top {len(sites_limited)} for metric '{metric}'"
            )

            return {
                "success": True,
                "metric": metric,
                "duration": duration,
                "sites": sites_limited,
                "total_sites": len(sites_limited),
            }

        except Exception as error:
            logger.error(f"Error fetching worst sites for metric '{metric}': {error}")
            return {
                "success": False,
                "metric": metric,
                "duration": duration,
                "sites": [],
                "error": str(error),
            }

    @staticmethod
    def _org_site_names(session: mistapi.APISession, org_id: str) -> dict[str, str]:
        names = {}
        page = 1
        while True:
            response = mistapi.api.v1.orgs.sites.listOrgSites(
                session, org_id, limit=1000, page=page
            )
            if not response or not hasattr(response, "data"):
                break
            sites = response.data or []
            if not sites:
                break
            for site in sites:
                names[site.get("id", "")] = site.get("name", "Unknown Site")
            if len(sites) < 1000:
                break
            page += 1
        return names

    @staticmethod
    def _get_worst_sites_response(
        session: mistapi.APISession, uri: str, query: dict[str, str]
    ) -> Any:
        max_retries = 3
        response = None
        for attempt in range(max_retries):
            response = session.mist_get(uri, query=query)
            if response and response.status_code == 200:
                break
            if response and response.status_code == 400 and attempt < max_retries - 1:
                wait_time = 2**attempt
                logger.warning(
                    f"API returned 400, retrying in {wait_time}s (attempt {attempt + 1}/{max_retries})"
                )
                time.sleep(wait_time)
            else:
                break
        return response


for _method_name, _method in list(MistConnection.__dict__.items()):
    if _method_name.startswith("_") or not inspect.isfunction(_method):
        continue
    setattr(MistConnection, _method_name, instrument_mist_method(_method))
