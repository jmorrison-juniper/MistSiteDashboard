#!/usr/bin/env python3
"""Debug script to check WAN SLE impact/distribution APIs from Mist."""
import os
import json
import mistapi
from dotenv import load_dotenv

load_dotenv()

session = mistapi.APISession(
    host=os.getenv('MIST_HOST', 'api.mist.com'),
    apitoken=os.getenv('MIST_APITOKEN')
)

self_response = mistapi.api.v1.self.self.getSelf(session)
privileges = self_response.data.get('privileges', [])

if privileges:
    org_id = privileges[0].get('org_id')
    sites_response = mistapi.api.v1.orgs.sites.listOrgSites(session, org_id, limit=5)
    sites = sites_response.data
    
    if sites:
        site_id = sites[0].get('id')
        site_name = sites[0].get('name')
        print(f'=== Testing site: {site_name} ({site_id}) ===')
        
        metric = 'wan-link-health'
        duration = '1d'
        
        # Test listSiteSleImpactedGateways (WAN Edges)
        print(f'\n=== listSiteSleImpactedGateways for {metric} ===')
        try:
            response = mistapi.api.v1.sites.sle.listSiteSleImpactedGateways(
                session, site_id, scope='site', scope_id=site_id,
                metric=metric, duration=duration
            )
            data = response.data
            print('Type:', type(data))
            if isinstance(data, list):
                print(f'Count: {len(data)}')
                for item in data[:3]:
                    print(f'  Gateway: {json.dumps(item, indent=4, default=str)}')
            else:
                print(json.dumps(data, indent=2, default=str))
        except Exception as e:
            print(f'Error: {e}')
            
        # Test listSiteSleImpactedInterfaces
        print(f'\n=== listSiteSleImpactedInterfaces for {metric} ===')
        try:
            response = mistapi.api.v1.sites.sle.listSiteSleImpactedInterfaces(
                session, site_id, scope='site', scope_id=site_id,
                metric=metric, duration=duration
            )
            data = response.data
            print('Type:', type(data))
            if isinstance(data, list):
                print(f'Count: {len(data)}')
                for item in data[:3]:
                    print(f'  Interface: {json.dumps(item, indent=4, default=str)}')
            else:
                print(json.dumps(data, indent=2, default=str))
        except Exception as e:
            print(f'Error: {e}')
            
        # Test listSiteSleImpactedApplications
        print(f'\n=== listSiteSleImpactedApplications for {metric} ===')
        try:
            response = mistapi.api.v1.sites.sle.listSiteSleImpactedApplications(
                session, site_id, scope='site', scope_id=site_id,
                metric=metric, duration=duration
            )
            data = response.data
            print('Type:', type(data))
            if isinstance(data, list):
                print(f'Count: {len(data)}')
                for item in data[:3]:
                    print(f'  App: {json.dumps(item, indent=4, default=str)}')
            else:
                print(json.dumps(data, indent=2, default=str))
        except Exception as e:
            print(f'Error: {e}')
            
        # Test listSiteSleImpactedWiredClients  
        print(f'\n=== listSiteSleImpactedWiredClients for {metric} ===')
        try:
            response = mistapi.api.v1.sites.sle.listSiteSleImpactedWiredClients(
                session, site_id, scope='site', scope_id=site_id,
                metric=metric, duration=duration
            )
            data = response.data
            print('Type:', type(data))
            if isinstance(data, list):
                print(f'Count: {len(data)}')
                for item in data[:3]:
                    print(f'  Client: {json.dumps(item, indent=4, default=str)}')
            else:
                print(json.dumps(data, indent=2, default=str))
        except Exception as e:
            print(f'Error: {e}')
