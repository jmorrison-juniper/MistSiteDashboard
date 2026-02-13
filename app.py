#!/usr/bin/env python3
"""
MistSiteDashboard - Juniper Mist Site Health Dashboard
A Flask-based web application for viewing device health and SLE metrics
for Juniper Mist sites.

Author: Joseph Morrison <jmorrison@juniper.net>
Version: 24.12.16.12.00
"""

import os
import sys
import logging
from datetime import datetime
from functools import wraps

from flask import Flask, render_template, jsonify, request
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

# Configure logging
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()

# Determine log handlers - only use file handler if logs directory exists and is writable
log_handlers = [logging.StreamHandler(sys.stdout)]
log_file_path = "/config/logs/app.log"
if os.path.exists("/config/logs") and os.access("/config/logs", os.W_OK):
    try:
        log_handlers.append(logging.FileHandler(log_file_path))
    except (PermissionError, IOError):
        pass  # Skip file logging if we can't write

logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=log_handlers
)
logger = logging.getLogger(__name__)

# Initialize Flask app
app = Flask(__name__)
app.config["SECRET_KEY"] = os.getenv("SECRET_KEY", os.urandom(24).hex())

# Import Mist connection module
from mist_connection import MistConnection

# Module-level singleton for Mist connection
_mist_connection: MistConnection | None = None


def get_mist_connection() -> MistConnection:
    """Get or create a Mist API connection."""
    global _mist_connection
    if _mist_connection is None:
        _mist_connection = MistConnection()
    return _mist_connection


@app.route("/")
def index():
    """Render the main dashboard page."""
    return render_template("index.html")


@app.route("/api/test-connection", methods=["POST"])
def test_connection():
    """Test the Mist API connection."""
    try:
        mist = get_mist_connection()
        result = mist.test_connection()
        if result["success"]:
            logger.info("Mist API connection test successful")
            return jsonify({"success": True, "message": "Connected to Mist API successfully", "org_name": result.get("org_name", "Unknown")})
        else:
            logger.warning(f"Mist API connection test failed: {result.get('error', 'Unknown error')}")
            return jsonify({"success": False, "error": result.get("error", "Connection failed")}), 400
    except Exception as error:
        logger.error(f"Connection test error: {error}")
        return jsonify({"success": False, "error": str(error)}), 500


@app.route("/api/sites", methods=["GET"])
def get_sites():
    """Get list of all sites in the organization."""
    try:
        mist = get_mist_connection()
        sites = mist.get_sites()
        logger.info(f"Retrieved {len(sites)} sites from Mist API")
        return jsonify({"success": True, "sites": sites})
    except Exception as error:
        logger.error(f"Error fetching sites: {error}")
        return jsonify({"success": False, "error": str(error)}), 500


@app.route("/api/sites/<site_id>/health", methods=["GET"])
def get_site_health(site_id):
    """Get health statistics for a specific site."""
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
    """Get SLE (Service Level Experience) metrics for a specific site."""
    try:
        mist = get_mist_connection()
        duration = request.args.get("duration", "1d")
        # Validate duration
        valid_durations = ["10m", "1h", "today", "1d", "1w"]
        if duration not in valid_durations:
            duration = "1d"
        sle_data = mist.get_site_sle(site_id, duration=duration)
        logger.info(f"Retrieved SLE data for site {site_id} (duration: {duration})")
        return jsonify({"success": True, "sle": sle_data, "duration": duration})
    except Exception as error:
        logger.error(f"Error fetching site SLE for {site_id}: {error}")
        return jsonify({"success": False, "error": str(error)}), 500


@app.route("/api/sites/<site_id>/devices", methods=["GET"])
def get_site_devices(site_id):
    """Get device statistics for a specific site."""
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
    """Get wireless client session history for the last 7 days."""
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
    """Get wired client information for the last 7 days."""
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
    """Get gateway WAN port status and configuration."""
    try:
        mist = get_mist_connection()
        gateways = mist.get_gateway_wan_status(site_id)
        logger.info(f"Retrieved WAN status for {len(gateways)} gateways for site {site_id}")
        return jsonify({"success": True, "gateways": gateways})
    except Exception as error:
        logger.error(f"Error fetching gateway WAN status for {site_id}: {error}")
        return jsonify({"success": False, "error": str(error)}), 500


# Detail page routes
@app.route("/ap-clients/<site_id>")
def ap_clients_page(site_id):
    """Render the AP clients history page."""
    return render_template("ap_clients.html", site_id=site_id)


@app.route("/switch-clients/<site_id>")
def switch_clients_page(site_id):
    """Render the switch wired clients page."""
    return render_template("switch_clients.html", site_id=site_id)


@app.route("/gateway-wan/<site_id>")
def gateway_wan_page(site_id):
    """Render the gateway WAN status page."""
    return render_template("gateway_wan.html", site_id=site_id)


# SLE detail page routes
@app.route("/sle/wifi/<site_id>")
def wifi_sle_page(site_id):
    """Render the WiFi SLE detail page."""
    return render_template("wifi_sle.html", site_id=site_id)


@app.route("/sle/wired/<site_id>")
def wired_sle_page(site_id):
    """Render the Wired SLE detail page."""
    return render_template("wired_sle.html", site_id=site_id)


@app.route("/sle/wan/<site_id>")
def wan_sle_page(site_id):
    """Render the WAN SLE detail page."""
    return render_template("wan_sle.html", site_id=site_id)


# SLE detail API routes
@app.route("/api/sites/<site_id>/sle/<category>", methods=["GET"])
def get_sle_details(site_id, category):
    """Get detailed SLE data for a specific category (wifi, wired, wan)."""
    try:
        mist = get_mist_connection()
        duration = request.args.get("duration", "1d")
        data = mist.get_sle_details(site_id, category, duration)
        return jsonify(data)
    except Exception as error:
        logger.error(f"Error fetching SLE details: {error}")
        return jsonify({"error": str(error)}), 500


@app.route("/health")
def health_check():
    """Health check endpoint for container orchestration."""
    return jsonify({"status": "healthy", "timestamp": datetime.utcnow().isoformat()})


if __name__ == "__main__":
    # Get port from environment or default to 5000
    port = int(os.getenv("PORT", 5000))
    debug = os.getenv("FLASK_DEBUG", "false").lower() == "true"
    
    logger.info(f"Starting MistSiteDashboard on port {port}")
    app.run(host="0.0.0.0", port=port, debug=debug)
