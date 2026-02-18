#!/usr/bin/env python3
"""
MistSiteDashboard - Juniper Mist Site Health Dashboard

A Flask-based web application for viewing device health and SLE (Service Level
Experience) metrics for Juniper Mist sites. This module serves as the main entry
point and provides REST API endpoints for the frontend dashboard.

Architecture Overview:
    - Flask serves both HTML templates and JSON API endpoints
    - MistConnection class handles all Mist Cloud API interactions
    - Templates use Bootstrap 5 with a dark theme for NOC environments
    - API responses are JSON-formatted for AJAX consumption

API Endpoints:
    GET  /                          - Main dashboard page
    GET  /api/test-connection       - Test Mist API connectivity
    GET  /api/sites                 - List all organization sites
    GET  /api/org/sle/<type>        - Get org-wide SLE insights (worst sites)
    GET  /api/sites/<id>/health     - Get device health for a site
    GET  /api/sites/<id>/sle        - Get SLE metrics for a site
    GET  /api/sites/<id>/devices    - Get device list for a site
    GET  /api/sites/<id>/wireless-clients - Get wireless client sessions
    GET  /api/sites/<id>/wired-clients    - Get wired client information
    GET  /api/sites/<id>/gateway-wan      - Get gateway WAN status
    GET  /api/sites/<id>/sle/<category>   - Get detailed SLE data
    GET  /health                    - Container health check endpoint

Environment Variables:
    MIST_APITOKEN  - Mist API token (required)
    MIST_ORG_ID    - Organization ID (optional, auto-detected)
    MIST_HOST      - API host (default: api.mist.com)
    PORT           - Web server port (default: 5000)
    LOG_LEVEL      - Logging level (default: INFO)
    SECRET_KEY     - Flask secret key (auto-generated if not set)

Author: Joseph Morrison <jmorrison@juniper.net>
Version: 24.12.16.12.00
License: CC BY-NC-SA 4.0 (https://creativecommons.org/licenses/by-nc-sa/4.0/)

Example:
    # Run the application directly
    $ python app.py
    
    # Or with environment variables
    $ PORT=8080 LOG_LEVEL=DEBUG python app.py
"""

# =============================================================================
# IMPORTS
# =============================================================================

# Standard library imports
import csv
import io
import os
import sys
import logging
from datetime import datetime
from functools import wraps

# Third-party imports
from flask import Flask, Response, render_template, jsonify, request
from dotenv import load_dotenv  # Loads environment variables from .env file

# =============================================================================
# ENVIRONMENT CONFIGURATION
# =============================================================================

# Load environment variables from .env file
# Container deployments can override the .env path using ENV_FILE environment variable
# This allows mounting secrets at custom paths (e.g., /run/secrets/.env)
env_file_path = os.getenv("ENV_FILE", ".env")
if os.path.exists(env_file_path):
    load_dotenv(env_file_path)
else:
    # Fall back to default .env in current directory for local development
    load_dotenv()

# =============================================================================
# LOGGING CONFIGURATION
# =============================================================================

# Get log level from environment, defaulting to INFO for production use
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()

# Configure log handlers
# - Always include stdout for container compatibility (logs to docker/podman logs)
# - Optionally add file handler for persistent logging in container environments
log_handlers = [logging.StreamHandler(sys.stdout)]
log_file_path = "/config/logs/app.log"

# Only attempt file logging if the logs directory exists and is writable
# This prevents errors in minimal container environments
if os.path.exists("/config/logs") and os.access("/config/logs", os.W_OK):
    try:
        log_handlers.append(logging.FileHandler(log_file_path))
    except (PermissionError, IOError):
        pass  # Gracefully skip file logging if we can't write

# Apply logging configuration globally
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=log_handlers
)

# Create module-level logger for this file
logger = logging.getLogger(__name__)

# =============================================================================
# FLASK APPLICATION INITIALIZATION
# =============================================================================

# Initialize Flask application with template folder auto-discovery
app = Flask(__name__)

# Set secret key for session security
# Uses environment variable if provided, otherwise generates a random key
# Note: Random key means sessions won't persist across restarts
app.config["SECRET_KEY"] = os.getenv("SECRET_KEY", os.urandom(24).hex())

# =============================================================================
# MIST API CONNECTION MANAGEMENT
# =============================================================================

# Import the Mist connection handler module
from mist_connection import MistConnection

# Module-level singleton for Mist connection
# Using singleton pattern to reuse API session across requests
# This improves performance by avoiding repeated authentication
_mist_connection: MistConnection | None = None


def get_mist_connection() -> MistConnection:
    """
    Get or create a Mist API connection singleton.
    
    This function implements lazy initialization of the MistConnection object.
    The connection is created on first use and reused for subsequent requests,
    which improves performance by maintaining a persistent API session.
    
    Returns:
        MistConnection: Singleton instance of the Mist API connection handler.
        
    Thread Safety:
        This implementation is not thread-safe. For multi-threaded deployments,
        consider using threading.Lock or Flask's application context.
    """
    global _mist_connection
    if _mist_connection is None:
        _mist_connection = MistConnection()
    return _mist_connection


# =============================================================================
# PAGE ROUTES - Serve HTML Templates
# =============================================================================

@app.route("/")
def index():
    """
    Render the main dashboard page.
    
    This is the primary entry point for users. The page displays:
    - Site selector dropdown
    - Device health cards (APs, Switches, Gateways)
    - SLE metric summaries (WiFi, Wired, WAN)
    - Time range selector for SLE data
    
    Returns:
        HTML: Rendered index.html template
    """
    return render_template("index.html")


@app.route("/sites/<site_id>")
def site_page(site_id):
    """
    Render the site-specific dashboard page.
    
    Displays detailed information for a single site including:
    - Device health cards (APs, Switches, Gateways)
    - SLE metric summaries with links to detail pages
    - Device tables for all device types
    
    Args:
        site_id: UUID of the site
        
    Returns:
        HTML: Rendered site.html template
    """
    return render_template("site.html", site_id=site_id)


# =============================================================================
# API ROUTES - JSON Data Endpoints
# =============================================================================

@app.route("/api/test-connection", methods=["POST"])
def test_connection():
    """
    Test the Mist API connection and return organization info.
    
    This endpoint verifies that the API token is valid and can connect
    to the Mist Cloud. It also auto-detects the organization ID if not
    explicitly configured.
    
    Returns:
        JSON: {
            "success": bool,
            "message": str (on success),
            "org_name": str (on success),
            "error": str (on failure)
        }
        
    Status Codes:
        200: Connection successful
        400: Connection failed (invalid credentials or permissions)
        500: Server error during connection attempt
    """
    try:
        mist = get_mist_connection()
        result = mist.test_connection()
        
        if result["success"]:
            logger.info("Mist API connection test successful")
            return jsonify({
                "success": True, 
                "message": "Connected to Mist API successfully", 
                "org_name": result.get("org_name", "Unknown")
            })
        else:
            logger.warning(f"Mist API connection test failed: {result.get('error', 'Unknown error')}")
            return jsonify({
                "success": False, 
                "error": result.get("error", "Connection failed")
            }), 400
            
    except Exception as error:
        logger.error(f"Connection test error: {error}")
        return jsonify({"success": False, "error": str(error)}), 500


@app.route("/api/sites", methods=["GET"])
def get_sites():
    """
    Get list of all sites in the organization.
    
    Retrieves all sites associated with the configured organization.
    Sites are sorted alphabetically by name for consistent display.
    
    Returns:
        JSON: {
            "success": bool,
            "sites": [{
                "id": str (site UUID),
                "name": str,
                "address": str,
                "country_code": str,
                "timezone": str
            }, ...]
        }
        
    Status Codes:
        200: Sites retrieved successfully
        500: Server error during retrieval
    """
    try:
        mist = get_mist_connection()
        sites = mist.get_sites()
        logger.info(f"Retrieved {len(sites)} sites from Mist API")
        return jsonify({"success": True, "sites": sites})
    except Exception as error:
        logger.error(f"Error fetching sites: {error}")
        return jsonify({"success": False, "error": str(error)}), 500


@app.route("/api/org/sle/<sle_type>", methods=["GET"])
def get_org_sle_insights(sle_type):
    """
    Get org-wide SLE insights for all sites by category.
    
    Retrieves SLE (Service Level Experience) data for all sites in the
    organization, sorted with worst-performing sites first. This enables
    a "Worst 100 Sites" dashboard view.
    
    Args:
        sle_type: SLE category to retrieve. Valid values: "wifi", "wired", "wan"
        
    Query Parameters:
        duration: Time range for metrics (default: "1d")
                  Valid values: "1d", "7d", "2w"
        
    Returns:
        JSON: {
            "success": bool,
            "sle_type": str,
            "duration": str,
            "sites": [{
                "site_id": str,
                "site_name": str,
                "num_aps": int,        # WiFi only
                "num_switches": int,   # Wired only
                "num_gateways": int,   # WAN only
                "num_clients": int,
                ... (SLE metrics specific to category)
            }, ...]
        }
        
    Status Codes:
        200: SLE data retrieved successfully
        400: Invalid sle_type parameter
        500: Server error during retrieval
    """
    try:
        mist = get_mist_connection()
        duration = request.args.get("duration", "1d")
        
        # Validate duration parameter
        valid_durations = ["1d", "7d", "2w"]
        if duration not in valid_durations:
            duration = "1d"
            
        # Validate SLE type parameter
        valid_types = ["wifi", "wired", "wan"]
        if sle_type not in valid_types:
            return jsonify({
                "success": False,
                "error": f"Invalid sle_type '{sle_type}'. Must be one of: {valid_types}"
            }), 400
            
        result = mist.get_org_sle_insights(sle_type, duration=duration)
        
        if result["success"]:
            logger.info(f"Retrieved org SLE insights for {sle_type} (found {len(result['sites'])} sites)")
            return jsonify(result)
        else:
            logger.warning(f"Failed to get org SLE insights: {result.get('error', 'Unknown error')}")
            return jsonify(result), 500
            
    except Exception as error:
        logger.error(f"Error fetching org SLE insights for {sle_type}: {error}")
        return jsonify({"success": False, "error": str(error)}), 500


@app.route("/api/org/sle/<sle_type>/metric/<metric>", methods=["GET"])
def get_org_sle_by_metric(sle_type, metric):
    """
    Get org-wide worst sites for a SPECIFIC SLE metric.
    
    Uses the worst-sites-by-sle-filtered API endpoint which returns sites
    sorted by worst performance for a specific metric. This is the correct
    endpoint for per-metric sorted data.
    
    Args:
        sle_type: SLE category (wifi, wired, wan) - for validation
        metric: Specific SLE metric name (e.g., time-to-connect, coverage)
        
    Query Parameters:
        duration: Time range (1h, 3h, 6h, 12h, 1d, 7d) - default "1d"
        
    Returns:
        JSON: {
            "success": bool,
            "metric": str,
            "duration": str,
            "sites": list of site data sorted by worst performers
        }
    """
    try:
        mist = get_mist_connection()
        duration = request.args.get("duration", "1d")
        
        # Validate metric belongs to the category
        valid_metrics = {
            "wifi": ["time-to-connect", "successful-connect", "coverage", "roaming", 
                     "throughput", "capacity", "ap-health", "ap-availability"],
            "wired": ["switch-health-v2", "switch-stc", "switch-throughput", "switch-bandwidth"],
            "wan": ["gateway-health", "wan-link-health", "application-health", "gateway-bandwidth"]
        }
        
        if sle_type not in valid_metrics:
            return jsonify({
                "success": False,
                "error": f"Invalid sle_type: {sle_type}. Must be wifi, wired, or wan"
            }), 400
        
        # Note: We don't strictly validate metric since API may support more
        result = mist.get_org_worst_sites_by_metric(metric, duration=duration)
        
        if result["success"]:
            logger.info(f"Retrieved worst sites for metric {metric} (found {len(result['sites'])} sites)")
            return jsonify(result)
        else:
            logger.warning(f"Failed to get worst sites for {metric}: {result.get('error', 'Unknown error')}")
            return jsonify(result), 500
            
    except Exception as error:
        logger.error(f"Error fetching worst sites for metric {metric}: {error}")
        return jsonify({"success": False, "error": str(error)}), 500


@app.route("/api/sites/<site_id>/health", methods=["GET"])
def get_site_health(site_id):
    """
    Get health statistics for a specific site.
    
    Retrieves device health metrics including connected/disconnected counts
    for APs, switches, and gateways. Also includes detailed device information
    such as model, IP, uptime, and firmware version.
    
    Args:
        site_id: UUID of the site to query
        
    Returns:
        JSON: {
            "success": bool,
            "health": {
                "aps": {"total": int, "connected": int, "disconnected": int, "devices": [...]},
                "switches": {"total": int, "connected": int, "disconnected": int, "devices": [...]},
                "gateways": {"total": int, "connected": int, "disconnected": int, "devices": [...]},
                "summary": {"total": int, "connected": int, "disconnected": int, "health_percentage": float}
            }
        }
        
    Status Codes:
        200: Health data retrieved successfully
        500: Server error during retrieval
    """
    try:
        mist = get_mist_connection()
        health_data = mist.get_site_health(site_id)
        logger.info(f"Retrieved health data for site {site_id}")
        return jsonify({"success": True, "health": health_data})
    except Exception as error:
        logger.error(f"Error fetching site health for {site_id}: {error}")
        return jsonify({"success": False, "error": str(error)}), 500


@app.route("/api/sites/<site_id>/sle", methods=["GET"])
def get_site_sle(site_id):
    """
    Get SLE (Service Level Experience) metrics for a specific site.
    
    SLE metrics provide insight into the quality of service experienced by
    users across WiFi, wired, and WAN networks. Metrics are calculated by
    the Mist Cloud based on device telemetry.
    
    Args:
        site_id: UUID of the site to query
        
    Query Parameters:
        duration: Time range for metrics (default: "1d")
                  Valid values: "10m", "1h", "today", "1d", "1w"
        
    Returns:
        JSON: {
            "success": bool,
            "sle": {
                "wifi": {"metrics": {...}, "available": bool},
                "wired": {"metrics": {...}, "available": bool},
                "wan": {"metrics": {...}, "available": bool}
            },
            "duration": str
        }
        
    Status Codes:
        200: SLE data retrieved successfully
        500: Server error during retrieval
    """
    try:
        mist = get_mist_connection()
        duration = request.args.get("duration", "1d")
        
        # Validate duration parameter to prevent API errors
        valid_durations = ["10m", "1h", "today", "1d", "1w"]
        if duration not in valid_durations:
            duration = "1d"  # Default to 24 hours if invalid
            
        sle_data = mist.get_site_sle(site_id, duration=duration)
        logger.info(f"Retrieved SLE data for site {site_id} (duration: {duration})")
        return jsonify({"success": True, "sle": sle_data, "duration": duration})
    except Exception as error:
        logger.error(f"Error fetching site SLE for {site_id}: {error}")
        return jsonify({"success": False, "error": str(error)}), 500


@app.route("/api/sites/<site_id>/devices", methods=["GET"])
def get_site_devices(site_id):
    """
    Get device statistics for a specific site.
    
    Retrieves detailed device information including CPU/memory utilization,
    connection status, firmware version, and device-specific metrics.
    
    Args:
        site_id: UUID of the site to query
        
    Query Parameters:
        type: Filter devices by type (default: "all")
              Valid values: "all", "ap", "switch", "gateway"
        
    Returns:
        JSON: {
            "success": bool,
            "devices": [{
                "id": str,
                "name": str,
                "type": str,
                "mac": str,
                "model": str,
                "status": str,
                "ip": str,
                "version": str,
                "uptime": int (seconds),
                ...
            }, ...]
        }
        
    Status Codes:
        200: Devices retrieved successfully
        500: Server error during retrieval
    """
    try:
        mist = get_mist_connection()
        device_type = request.args.get("type", "all")
        devices = mist.get_site_devices(site_id, device_type=device_type)
        logger.info(f"Retrieved {len(devices)} devices for site {site_id}")
        return jsonify({"success": True, "devices": devices})
    except Exception as error:
        logger.error(f"Error fetching site devices for {site_id}: {error}")
        return jsonify({"success": False, "error": str(error)}), 500


@app.route("/api/sites/<site_id>/wireless-clients", methods=["GET"])
def get_wireless_client_sessions(site_id):
    """
    Get wireless client session history for the last 7 days.
    
    Retrieves comprehensive wireless client data by merging information from
    multiple API sources (client stats, client search, session history) to
    provide the most complete view of wireless clients.
    
    Args:
        site_id: UUID of the site to query
        
    Returns:
        JSON: {
            "success": bool,
            "sessions": [{
                "mac": str,
                "hostname": str,
                "ip": str,
                "username": str,
                "ssid": str,
                "ap": str (AP MAC address),
                "band": str (2.4/5/6 GHz),
                "os": str,
                "manufacture": str,
                "last_seen": int (epoch timestamp),
                "rssi": int (signal strength),
                "is_connected": bool
            }, ...]
        }
        
    Status Codes:
        200: Client data retrieved successfully
        500: Server error during retrieval
    """
    try:
        mist = get_mist_connection()
        sessions = mist.get_wireless_client_sessions(site_id)
        logger.info(f"Retrieved {len(sessions)} wireless client sessions for site {site_id}")
        return jsonify({"success": True, "sessions": sessions})
    except Exception as error:
        logger.error(f"Error fetching wireless client sessions for {site_id}: {error}")
        return jsonify({"success": False, "error": str(error)}), 500


@app.route("/api/sites/<site_id>/wired-clients", methods=["GET"])
def get_wired_clients(site_id):
    """
    Get wired client information for the last 7 days.
    
    Retrieves wired client data including DHCP information, switch port
    assignments, and connection status from the Mist Cloud.
    
    Args:
        site_id: UUID of the site to query
        
    Returns:
        JSON: {
            "success": bool,
            "clients": [{
                "mac": str,
                "hostname": str,
                "ip": str,
                "username": str,
                "connected_time": int (epoch timestamp),
                "last_seen": int (epoch timestamp),
                "device_type": str,
                "is_connected": bool,
                "switch_mac": str,
                "port_id": str
            }, ...]
        }
        
    Status Codes:
        200: Client data retrieved successfully
        500: Server error during retrieval
    """
    try:
        mist = get_mist_connection()
        clients = mist.get_wired_clients(site_id)
        logger.info(f"Retrieved {len(clients)} wired clients for site {site_id}")
        return jsonify({"success": True, "clients": clients})
    except Exception as error:
        logger.error(f"Error fetching wired clients for {site_id}: {error}")
        return jsonify({"success": False, "error": str(error)}), 500


@app.route("/api/sites/<site_id>/gateway-wan", methods=["GET"])
def get_gateway_wan_status(site_id):
    """
    Get gateway WAN port status and configuration.
    
    Retrieves comprehensive gateway information including:
    - WAN port status (up/down, IP, traffic stats)
    - VPN peer status (latency, jitter, loss, MOS)
    - BGP peer status (neighbor info, route counts)
    
    Args:
        site_id: UUID of the site to query
        
    Returns:
        JSON: {
            "success": bool,
            "gateways": [{
                "id": str,
                "name": str,
                "mac": str,
                "model": str,
                "status": str,
                "wan_ports": [{...}],
                "vpn_peers": [{...}],
                "bgp_peers": [{...}]
            }, ...]
        }
        
    Status Codes:
        200: Gateway data retrieved successfully
        500: Server error during retrieval
    """
    try:
        mist = get_mist_connection()
        gateways = mist.get_gateway_wan_status(site_id)
        logger.info(f"Retrieved WAN status for {len(gateways)} gateways for site {site_id}")
        return jsonify({"success": True, "gateways": gateways})
    except Exception as error:
        logger.error(f"Error fetching gateway WAN status for {site_id}: {error}")
        return jsonify({"success": False, "error": str(error)}), 500


# =============================================================================
# DETAIL PAGE ROUTES - Client and Device Detail Views
# =============================================================================

@app.route("/ap-clients/<site_id>")
def ap_clients_page(site_id):
    """
    Render the AP clients history page.
    
    Displays a detailed table of wireless client sessions over the last 7 days,
    including signal strength, SSID, band, and connection history.
    
    Args:
        site_id: UUID of the site (passed to template for API calls)
        
    Returns:
        HTML: Rendered ap_clients.html template
    """
    return render_template("ap_clients.html", site_id=site_id)


@app.route("/switch-clients/<site_id>")
def switch_clients_page(site_id):
    """
    Render the switch wired clients page.
    
    Displays a detailed table of wired clients connected to switches,
    including port info, VLAN, and DHCP details.
    
    Args:
        site_id: UUID of the site (passed to template for API calls)
        
    Returns:
        HTML: Rendered switch_clients.html template
    """
    return render_template("switch_clients.html", site_id=site_id)


@app.route("/gateway-wan/<site_id>")
def gateway_wan_page(site_id):
    """
    Render the gateway WAN status page.
    
    Displays detailed WAN port status, VPN peer metrics, and BGP peer
    information for all gateways at the site.
    
    Args:
        site_id: UUID of the site (passed to template for API calls)
        
    Returns:
        HTML: Rendered gateway_wan.html template
    """
    return render_template("gateway_wan.html", site_id=site_id)


# =============================================================================
# SLE DETAIL PAGE ROUTES - Service Level Experience Analysis
# =============================================================================

@app.route("/sle/wifi/<site_id>")
def wifi_sle_page(site_id):
    """
    Render the WiFi SLE detail page.
    
    Displays detailed WiFi SLE metrics including:
    - Coverage, Capacity, Time-to-Connect
    - Roaming, Throughput, AP Availability
    - Classifier breakdown with impact analysis
    
    Args:
        site_id: UUID of the site (passed to template for API calls)
        
    Returns:
        HTML: Rendered wifi_sle.html template
    """
    return render_template("wifi_sle.html", site_id=site_id)


@app.route("/sle/wired/<site_id>")
def wired_sle_page(site_id):
    """
    Render the Wired SLE detail page.
    
    Displays detailed Wired SLE metrics including:
    - Switch Health, Switch Throughput
    - Switch STC (Successful Time to Connect)
    - Classifier breakdown with impact analysis
    
    Args:
        site_id: UUID of the site (passed to template for API calls)
        
    Returns:
        HTML: Rendered wired_sle.html template
    """
    return render_template("wired_sle.html", site_id=site_id)


@app.route("/sle/wan/<site_id>")
def wan_sle_page(site_id):
    """
    Render the WAN SLE detail page.
    
    Displays detailed WAN SLE metrics including:
    - Gateway Health, WAN Link Health
    - Application Health, Gateway Bandwidth
    - Classifier breakdown with impact analysis
    
    Args:
        site_id: UUID of the site (passed to template for API calls)
        
    Returns:
        HTML: Rendered wan_sle.html template
    """
    return render_template("wan_sle.html", site_id=site_id)


# =============================================================================
# SLE DETAIL API ROUTES - JSON Data for SLE Analysis Pages
# =============================================================================

@app.route("/api/sites/<site_id>/sle/<category>", methods=["GET"])
def get_sle_details(site_id, category):
    """
    Get detailed SLE data for a specific category.
    
    Retrieves comprehensive SLE information including all metrics for the
    category, their classifiers, and impact data for the detail pages.
    
    Args:
        site_id: UUID of the site to query
        category: SLE category ("wifi", "wired", or "wan")
        
    Query Parameters:
        duration: Time range for analysis (default: "1d")
                  Valid values: "1h", "1d", "1w"
        
    Returns:
        JSON: {
            "category": str,
            "duration": str,
            "metrics": {
                "metric_name": {
                    "name": str,
                    "sle_value": float (0-100),
                    "classifiers": [{...}],
                    "impact": {...}
                }, ...
            }
        }
        
    Status Codes:
        200: SLE details retrieved successfully
        500: Server error during retrieval
    """
    try:
        mist = get_mist_connection()
        duration = request.args.get("duration", "1d")
        data = mist.get_sle_details(site_id, category, duration)
        return jsonify(data)
    except Exception as error:
        logger.error(f"Error fetching SLE details: {error}")
        return jsonify({"error": str(error)}), 500


@app.route("/api/sites/<site_id>/sle/impact/<metric>/<classifier>", methods=["GET"])
def get_classifier_impact(site_id, metric, classifier):
    """
    Get detailed impact data for a specific metric/classifier combination.
    
    Retrieves breakdown of affected devices, WLANs, device types, etc.
    for a specific SLE classifier to help identify root causes.
    
    Args:
        site_id: UUID of the site to query
        metric: SLE metric name (e.g., "coverage", "capacity")
        classifier: Classifier name (e.g., "client-usage", "interference")
        
    Query Parameters:
        duration: Time range for analysis (default: "1d")
        
    Returns:
        JSON: {
            "metric": str,
            "classifier": str,
            "aps": [{"mac": str, "name": str, "degraded": int, "total": int}, ...],
            "wlans": [{"id": str, "name": str, "degraded": int, "total": int}, ...],
            "device_types": [{...}],
            "device_os": [{...}],
            "bands": [{...}]
        }
        
    Status Codes:
        200: Impact data retrieved successfully
        500: Server error during retrieval
    """
    try:
        mist = get_mist_connection()
        duration = request.args.get("duration", "1d")
        data = mist.get_classifier_impact_details(site_id, metric, classifier, duration)
        return jsonify(data)
    except Exception as error:
        logger.error(f"Error fetching classifier impact: {error}")
        return jsonify({"error": str(error)}), 500


@app.route("/api/sites/<site_id>/sle/<metric>/impacted/<item_type>", methods=["GET"])
def get_sle_impacted_items(site_id, metric, item_type):
    """
    Get detailed impacted items (Distribution/Affected Items) for an SLE metric.
    
    Retrieves rich distribution data showing which gateways, interfaces,
    applications, or clients are affected by SLE degradation. Matches the
    Distribution and Affected Items tabs in Mist dashboard.
    
    Args:
        site_id: UUID of the site to query
        metric: SLE metric name (e.g., 'wan-link-health', 'gateway-health')
        item_type: Type of impacted items. Valid values:
            - 'gateways': WAN Edges with failure rates
            - 'interfaces': Gateway interfaces with failure rates
            - 'applications': Applications with failure rates
            - 'clients': Wired clients affected
            - 'wireless_clients': Wireless clients affected
        
    Query Parameters:
        duration: Time range (default: "1d"). Valid: "1d", "7d", "2w"
        classifier: Optional classifier filter (e.g., "network-latency")
        
    Returns:
        JSON: {
            "total_count": int,
            "metric": str,
            "classifier": str,
            "items": [
                {
                    "name": str,
                    "degraded": int,
                    "total": int,
                    "failure_rate": float (0-100),
                    "overall_impact": float (percentage of total degradation),
                    ... (additional fields vary by item_type)
                }, ...
            ]
        }
        
    Status Codes:
        200: Impacted items retrieved successfully
        400: Invalid item_type
        500: Server error during retrieval
    """
    valid_types = ["gateways", "interfaces", "applications", "clients", "wireless_clients"]
    if item_type not in valid_types:
        return jsonify({
            "error": f"Invalid item_type '{item_type}'. Must be one of: {valid_types}"
        }), 400
    
    try:
        mist = get_mist_connection()
        duration = request.args.get("duration", "1d")
        classifier = request.args.get("classifier")
        data = mist.get_sle_impacted_items(site_id, metric, item_type, duration, classifier)
        return jsonify(data)
    except Exception as error:
        logger.error(f"Error fetching impacted {item_type}: {error}")
        return jsonify({"error": str(error)}), 500


# =============================================================================
# SLE CSV EXPORT ENDPOINT
# =============================================================================

@app.route("/api/sites/<site_id>/sle/<category>/csv", methods=["GET"])
def export_sle_csv(site_id, category):
    """
    Export SLE data for a category as CSV.
    
    Generates a CSV file containing all metrics and their classifiers
    for the specified SLE category (wifi, wired, or wan).
    
    Args:
        site_id: UUID of the site to query
        category: SLE category ("wifi", "wired", or "wan")
        
    Query Parameters:
        duration: Time range for analysis (default: "1d")
        
    Returns:
        CSV file download with columns:
        - Metric, SLE Value (%), Classifier, Contribution (%), Impact Count
        
    Status Codes:
        200: CSV generated successfully
        400: Invalid category
        500: Server error during generation
    """
    valid_categories = ["wifi", "wired", "wan"]
    if category not in valid_categories:
        return jsonify({
            "error": f"Invalid category: {category}. Must be one of: {valid_categories}"
        }), 400
    
    try:
        mist = get_mist_connection()
        duration = request.args.get("duration", "1d")
        
        # Get site name for the filename
        site_info = mist.get_site_info(site_id)
        site_name = site_info.get("name", site_id)
        # Sanitize site name for filename (remove special chars, replace spaces)
        safe_site_name = "".join(c if c.isalnum() or c in "-_" else "_" for c in site_name)
        
        data = mist.get_sle_details(site_id, category, duration)
        
        # Build CSV content
        output = io.StringIO()
        writer = csv.writer(output)
        
        # Write header
        writer.writerow([
            "Metric",
            "SLE Value (%)",
            "Classifier",
            "Contribution (%)",
            "Impact Count"
        ])
        
        # Flatten metrics and classifiers into rows
        metrics = data.get("metrics", {})
        for metric_name, metric_data in metrics.items():
            sle_value = metric_data.get("sle_value")
            sle_display = f"{sle_value:.1f}" if sle_value is not None else "N/A"
            
            classifiers = metric_data.get("classifiers", [])
            if classifiers:
                for classifier in classifiers:
                    classifier_name = classifier.get("name", "Unknown")
                    contribution = classifier.get("contribution", 0)
                    impact_count = classifier.get("impact_count", classifier.get("degraded", 0))
                    writer.writerow([
                        metric_name,
                        sle_display,
                        classifier_name,
                        f"{contribution:.1f}" if contribution else "0.0",
                        impact_count
                    ])
            else:
                # Metric with no classifiers - write single row
                writer.writerow([metric_name, sle_display, "", "", ""])
        
        # Generate response with CSV content
        csv_content = output.getvalue()
        output.close()
        
        filename = f"sle_{category}_{safe_site_name}_{duration}.csv"
        
        return Response(
            csv_content,
            mimetype="text/csv",
            headers={"Content-Disposition": f"attachment; filename={filename}"}
        )
        
    except Exception as error:
        logger.error(f"Error exporting SLE CSV: {error}")
        return jsonify({"error": str(error)}), 500


# =============================================================================
# HEALTH CHECK ENDPOINT - Container Orchestration Support
# =============================================================================

@app.route("/health")
def health_check():
    """
    Health check endpoint for container orchestration.
    
    This endpoint is used by Docker/Kubernetes health probes to determine
    if the application is running and responsive. It returns a simple JSON
    response without requiring API connectivity.
    
    Returns:
        JSON: {
            "status": "healthy",
            "timestamp": str (ISO 8601 format)
        }
        
    Example:
        curl http://localhost:5000/health
        {"status": "healthy", "timestamp": "2024-12-16T12:00:00"}
    """
    return jsonify({"status": "healthy", "timestamp": datetime.utcnow().isoformat()})


# =============================================================================
# APPLICATION ENTRY POINT
# =============================================================================

if __name__ == "__main__":
    # Get server configuration from environment variables
    # PORT: Web server port (default 5000 for local development)
    # FLASK_DEBUG: Enable debug mode (auto-reload, detailed errors)
    port = int(os.getenv("PORT", 5000))
    debug = os.getenv("FLASK_DEBUG", "false").lower() == "true"
    
    logger.info(f"Starting MistSiteDashboard on port {port}")
    
    # Bind to 0.0.0.0 to accept connections from all interfaces
    # This is required for container deployments where localhost != host network
    app.run(host="0.0.0.0", port=port, debug=debug)
