# Implementation plan: MistSiteDashboard performance monitoring

## Summary
This plan adds an instrumentation layer to the Flask dashboard and its Mist API client so the team can measure request latency, API fan-out cost, and payload-processing overhead. The goal is to identify the real CPU and network bottlenecks before making any optimization changes.

## Design principles
- Monitors must be additive and low-cost.
- The instrumentation must not change HTTP behavior or return payloads.
- Each route and central helper must emit the same timing fields.
- Use the right monitor for the right layer: wall time, call attribution, and memory tracing where appropriate.

## Hook matrix

### `app.py`
- `index()`
  - monitor: render latency and template size
- `test_connection()`
  - monitor: full auth/session test latency
- `get_sites()`
  - monitor: request duration and number of site records returned
- `get_site_health(site_id)`
  - monitor: per-route latency plus downstream call cost
- `get_site_sle(site_id)`
  - monitor: query parameter validation time and result payload size
- `get_site_devices(site_id)`
  - monitor: device list length and processing time
- `get_wireless_client_sessions(site_id)`
  - monitor: client session count and serialization cost
- `get_wired_clients(site_id)`
  - monitor: list size and data shaping cost
- `get_gateway_wan_status(site_id)`
  - monitor: WAN status distribution and response size
- `health_check()`
  - monitor: lightweight readiness check latency

### `mist_connection.py`
- `MistConnection.__init__`
  - monitor: configuration and initialization cost
- `_get_session()`
  - monitor: session creation cache hit/miss and session reuse cost
- `test_connection()`
  - monitor: connection validation and org lookup latency
- `get_sites()`
  - monitor: API call time, page count, site sorting time
- `get_site_health(site_id)`
  - monitor: template fetch, WLAN mapping, device fetch, and summary build cost separately
- `get_wireless_client_sessions` / `get_wired_clients` / `get_gateway_wan_status`
  - monitor: API call latency, parse time, result count, serialization time

## Metrics to capture
- `duration_ms`
- `payload_bytes`
- `items_returned`
- `rows_processed`
- `retry_count`
- `cache_hit`
- `status`
- `route`
- `site_id`
- `error_class`
- `peak_bytes`

## Validation plan
- Baseline the app with a small, medium, and large site dataset.
- Capture request-by-request histograms for route latency.
- Capture stage-by-stage timing for `get_site_health` and `get_sites`.
- Use `tracemalloc` on the largest site path to confirm dict/list churn.
- Compare benchmark outputs before and after optimization.

## Deliverables
- Repo-local issue and tracking files under `specs/2449-mistsite-performance-monitoring/`
- Initial measurement baseline for route and API-call cost
- A concrete optimization-ready profile for future changes
