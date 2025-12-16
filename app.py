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
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("/config/logs/app.log") if os.path.exists("/config") else logging.NullHandler()
    ]
)
logger = logging.getLogger(__name__)

# Initialize Flask app
app = Flask(__name__)
app.config["SECRET_KEY"] = os.getenv("SECRET_KEY", os.urandom(24).hex())

# Import Mist connection module
from mist_connection import MistConnection


def get_mist_connection():
    """Get or create a Mist API connection."""
    if not hasattr(app, "mist_connection"):
        app.mist_connection = MistConnection()
    return app.mist_connection


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
        sle_data = mist.get_site_sle(site_id)
        logger.info(f"Retrieved SLE data for site {site_id}")
        return jsonify({"success": True, "sle": sle_data})
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
