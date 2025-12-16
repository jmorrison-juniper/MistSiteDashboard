# MistSiteDashboard

View device health and SLE (Service Level Experience) metrics for Juniper Mist sites in a clean, modern web interface.

![Dashboard Preview](https://img.shields.io/badge/Status-Development-yellow)
![License](https://img.shields.io/badge/License-MIT-blue)
![Container](https://img.shields.io/badge/Container-ghcr.io-purple)

## Features

- **Site Selection**: Browse and search through all sites in your Mist organization
- **Device Health**: Real-time status of Access Points, Switches, and Gateways
- **SLE Metrics**: WiFi, Wired, and WAN Service Level Experience scores
- **Device Details**: Drill down into individual device status, uptime, and version info
- **Dark Mode**: Modern dark theme optimized for NOC environments
- **Container Ready**: Multi-architecture Docker/Podman support (amd64/arm64)

## Quick Start

### Docker

```bash
docker run -d \
  --name mistsitedashboard \
  -p 5000:5000 \
  -e MIST_APITOKEN=your_api_token_here \
  -e MIST_ORG_ID=your_org_id_here \
  ghcr.io/jmorrison-juniper/mistsitedashboard:latest
```

### Docker Compose

```yaml
services:
  mistsitedashboard:
    image: ghcr.io/jmorrison-juniper/mistsitedashboard:latest
    container_name: mistsitedashboard
    ports:
      - "5000:5000"
    environment:
      - MIST_APITOKEN=your_api_token_here
      - MIST_ORG_ID=your_org_id_here
      - TZ=UTC
    volumes:
      - ./config:/config
    restart: unless-stopped
```

Then run: `docker-compose up -d`

Access at: `http://your-server-ip:5000`

## Configuration

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| MIST_APITOKEN | Yes | - | Your Mist API token |
| MIST_ORG_ID | No | auto-detect | Organization ID |
| MIST_HOST | No | api.mist.com | API host (api.eu.mist.com for EU) |
| PORT | No | 5000 | Web interface port |
| LOG_LEVEL | No | INFO | DEBUG, INFO, WARNING, ERROR |
| TZ | No | UTC | Container timezone |

### Getting Your Mist API Token

1. Log into the Mist dashboard at https://manage.mist.com
2. Navigate to **Organization** > **Settings** > **API Token**
3. Click **Create Token** and copy the token value

### Finding Your Organization ID

The Organization ID can be found in the Mist dashboard URL:
```
https://manage.mist.com/admin/?org_id=YOUR_ORG_ID_HERE
```

Or leave it empty and the dashboard will auto-detect your first available organization.

## Local Development

```bash
# Clone the repository
git clone https://github.com/jmorrison-juniper/MistSiteDashboard.git
cd MistSiteDashboard

# Create virtual environment
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt

# Copy and configure environment
cp .env.example .env
# Edit .env with your Mist credentials

# Run the application
python app.py
```

## API Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/` | GET | Main dashboard interface |
| `/api/test-connection` | POST | Test Mist API connection |
| `/api/sites` | GET | List all organization sites |
| `/api/sites/<id>/health` | GET | Get device health for a site |
| `/api/sites/<id>/sle` | GET | Get SLE metrics for a site |
| `/api/sites/<id>/devices` | GET | Get device details for a site |
| `/health` | GET | Container health check |

## Architecture

```
MistSiteDashboard/
├── app.py                 # Flask application & routes
├── mist_connection.py     # Mist API client
├── templates/
│   └── index.html         # Dashboard UI
├── requirements.txt       # Python dependencies
├── Dockerfile             # Container build
├── docker-compose.yml     # Container orchestration
└── .github/
    └── workflows/
        └── container-build.yml  # CI/CD pipeline
```

## Changelog

```json
{
  "24.12.16": {
    "feature-additions": [
      "Initial release",
      "Site selection with search/filter",
      "Device health display (APs, Switches, Gateways)",
      "SLE metrics (WiFi, Wired, WAN)",
      "Device detail tables with status, uptime, version",
      "Dark mode UI optimized for NOC environments"
    ],
    "container": [
      "Multi-architecture support (amd64/arm64)",
      "GitHub Container Registry publishing",
      "Non-root container user for security"
    ]
  }
}
```

## Related Projects

- [MistHelper](https://github.com/jmorrison-juniper/MistHelper) - Comprehensive Mist API data export tool
- [mistapi_python](https://github.com/tmunzer/mistapi_python) - Python SDK for Mist API (by Thomas Munzer)

## License

MIT

---

Made for Juniper Mist network operations.
