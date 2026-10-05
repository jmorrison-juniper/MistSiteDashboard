# Offline screenshot provenance

The four `screenshots/offline-*.png` images were captured on 2026-10-05 UTC
from this repository's real Flask application and unmodified HTML templates.
Chromium rendered the pages at 1440 x 1080; full-page PNGs are not composites,
illustrations, or edited screenshots. All organization, site, device and client
data are synthetic. The demo uses the existing benchmark fixture, extended with
display-ready SLE scores and example clients.

| Image | User screen | Local route |
| --- | --- | --- |
| `offline-organization.png` | Organization's worst-site SLE dashboard | `/` |
| `offline-site.png` | Site device health and SLE overview | `/sites/site-1` |
| `offline-wireless-clients.png` | Wireless client history | `/ap-clients/site-1` |
| `offline-wired-clients.png` | Wired client history | `/switch-clients/site-1` |

## Reproduce

Use Python 3.13 or newer. Install the application's dependencies from
`requirements.txt`, or run the isolated UV commands below. No `.env`, Mist
credentials or API access are needed. `OfflineDemo` replaces connection lookup
before serving any requests and binds only to loopback.

```bash
uv run --no-project --python 3.13 --with flask --with mistapi --with python-dotenv \
  python -m scripts.offline_demo
```

In a second terminal:

```bash
uv run --no-project --python 3.13 --with playwright playwright install chromium
uv run --no-project --python 3.13 --with playwright python -m scripts.capture_screenshots
```

Stop the demo with Ctrl+C after capture. Screens are written directly to
`docs/screenshots/`. The capture checks HTTP success, loaded data and JavaScript
errors before saving each image.

**Network boundary:** offline here means no connection to Mist or customer
systems. The existing templates load Bootstrap and icons from public CDNs.
Browser requests are restricted to loopback, `cdn.jsdelivr.net` and
`cdnjs.cloudflare.com`; unexpected hosts fail capture. Installing dependencies,
Chromium and fetching these presentation assets needs internet access (or an
existing local cache).

The older screenshots preserved in the [user guide](user-guide.md#screenshots)
predate this offline capture; their provenance is not claimed by this procedure.

## Verification

Run the tests with no Mist credentials:

```bash
uv run --no-project --python 3.13 --with flask --with mistapi --with python-dotenv \
  --with pytest python -m pytest
```

The documentation tests enforce the six-question landing page, local links and
image signatures. Offline route tests reject real Mist connection construction
and exercise the pages and data routes used by the screenshots.
