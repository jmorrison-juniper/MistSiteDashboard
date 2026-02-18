#!/usr/bin/env python3
"""
Debug script for testing SLE (Service Level Experience) classifier API responses.

This utility script is used for development and troubleshooting of the SLE
data retrieval logic in the MistSiteDashboard application. It connects directly
to the Mist Cloud API and tests various SLE-related endpoints.

Purpose:
    - Verify SLE API responses are returning expected data structure
    - Test specific metric and classifier combinations
    - Debug impact summary data for APs and device types
    - Validate API authentication and permissions

Usage:
    # Ensure .env file contains MIST_APITOKEN and MIST_HOST
    python debug_sle.py

Environment Variables Required:
    MIST_APITOKEN: API token with read access to site SLE data
    MIST_HOST: Mist API host (default: 'api.mist.com')

Output:
    Prints SLE impact summary data to console showing:
    - Number of affected APs with degraded/total counts
    - Device type distribution for affected clients
    - Raw API response data for inspection

Note:
    This is a development utility and is NOT used by the main application.
    It automatically uses the first site in the first organization the
    API token has access to.
"""

import os
import json
import mistapi
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

# =============================================================================
# Initialize Mist API session using environment credentials
# =============================================================================
session = mistapi.APISession(
    host=os.getenv('MIST_HOST', 'api.mist.com'),
    apitoken=os.getenv('MIST_APITOKEN')
)

# =============================================================================
# Get user's organization and site for testing
# =============================================================================
# Use getSelf to determine which orgs the API token has access to
self_response = mistapi.api.v1.self.self.getSelf(session)
privileges = self_response.data.get('privileges', [])
if privileges:
    # Use the first organization from the token's privileges
    org_id = privileges[0].get('org_id')
    
    # Get the first site in the organization for testing
    sites_response = mistapi.api.v1.orgs.sites.listOrgSites(session, org_id, limit=5)
    sites = sites_response.data
    
    if sites:
        site_id = sites[0].get('id')
        site_name = sites[0].get('name')
        print(f'=== Testing site: {site_name} ({site_id}) ===')
        
        # ---------------------------------------------------------------------
        # Test configuration - adjust these to test different scenarios
        # ---------------------------------------------------------------------
        test_metric = 'capacity'        # SLE metric to test
        test_classifier = 'client-usage'  # Classifier that typically has data
        
        try:
            # -----------------------------------------------------------------
            # Test 1: Metric-level impact summary (no classifier filter)
            # -----------------------------------------------------------------
            print(f'\n=== Impact Summary for {test_metric} (metric-level) ===')
            impact_response = mistapi.api.v1.sites.sle.getSiteSleImpactSummary(
                session, site_id, scope='site', scope_id=site_id,
                metric=test_metric, duration='1d'
            )
            data = impact_response.data
            print(f"APs affected: {len(data.get('ap', []))}")
            for ap in data.get('ap', [])[:3]:  # Show first 3 APs
                print(f"  - {ap.get('name')}: {ap.get('degraded')}/{ap.get('total')} degraded")
            
            # -----------------------------------------------------------------
            # Test 2: Classifier-level impact summary (filtered by classifier)
            # -----------------------------------------------------------------
            print(f'\n=== Impact Summary for {test_metric}/{test_classifier} (classifier-level) ===')
            clf_impact_response = mistapi.api.v1.sites.sle.getSiteSleImpactSummary(
                session, site_id, scope='site', scope_id=site_id,
                metric=test_metric, classifier=test_classifier, duration='1d'
            )
            clf_data = clf_impact_response.data
            
            # Display AP impact
            print(f"APs affected: {len(clf_data.get('ap', []))}")
            for ap in clf_data.get('ap', [])[:3]:
                print(f"  - {ap.get('name')}: {ap.get('degraded')}/{ap.get('total')} degraded")
            
            # Display device type distribution
            print(f"Users by device_type: {len(clf_data.get('device_type', []))}")
            for dt in clf_data.get('device_type', [])[:3]:
                print(f"  - {dt.get('name')}: {dt.get('degraded')}/{dt.get('total')} degraded")
                
        except Exception as e:
            # Print full traceback for debugging
            import traceback
            print(f'Error: {e}')
            traceback.print_exc()
