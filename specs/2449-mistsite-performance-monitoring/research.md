# MistSiteDashboard performance instrumentation research

## Objective
Map the dashboard application to a precise latency and payload-tracing plan so each request and Mist API workflow can be measured and optimized with evidence.

## Source scan findings
The application is small but the expensive work sits in a few request-handler and connection methods:

- `app.py` defines request handlers for `/api/sites`, `/api/sites/<site_id>/health`, `/api/sites/<site_id>/sle`, `/api/sites/<site_id>/devices`, `/api/sites/<site_id>/wireless-clients`, `/api/sites/<site_id>/wired-clients`, and `/api/sites/<site_id>/gateway-wan`.
- `mist_connection.py` centralizes all API auth, session creation, and remote fetch behavior.

The major cost sources are:
- Mist API calls and remote request latency
- JSON decoding and normalization of site, device, and client payloads
- per-request sorting and filtering
- large result shaping before JSON serialization
- repeated session or org lookup work across routes

## Hook design

### Flask request layer
Add a request timing wrapper in `app.py` for all route handlers.

Monitor:
- `route`, `method`, `duration_ms`
- `response_status`, `payload_bytes`, `items_returned`
- `query_params` (sanitized)
- `error_class` if exceptions occur

Use:
- `time.perf_counter()` around each route body
- response-size capture after `jsonify` or render
- structured logger lines or `logging` metrics output

### MistConnection API layer
Add the exact instrumentation around the central Mist API boundary.

Targets:
- `MistConnection.__init__`
- `MistConnection._get_session`
- `MistConnection.test_connection`
- `MistConnection.get_sites`
- `MistConnection.get_site_health`
- `MistConnection.get_wireless_client_sessions`
- `MistConnection.get_wired_clients`
- `MistConnection.get_gateway_wan_status`

Monitor:
- session creation time
- API call duration per endpoint
- heat of org/site lookups
- duplicates in template/WLAN mapping for `get_site_health`
- row count per returned list
- payload parse and serialization time

### High-cost method decomposition for `get_site_health`
This method is the most costly in the dashboard because it does multiple remote fetches and nested shaping.

Break down into sub-metrics:
- template fetch latency
- Wi-Fi template and WLAN mapping time
- site device stats fetch latency
- device categorization time
- summary-building time
- final JSON serialization time

This is the most important place for a per-stage profile because it contains network I/O plus in-process aggregation.

## Correct monitor types
- Use `time.perf_counter()` for wall-clock across route and API method calls.
- Use `tracemalloc` on `get_site_health` and list-returning methods to spot large dict/list churn.
- Use `cProfile` for a small sampling run once the hook layer is in place.
- Use lightweight structured logging for production-friendly operational metrics.
- Use Prometheus or histogram-style counters if the dashboard later adds a metrics endpoint.

## Recommended metric schema
- `event_name`
- `module`
- `class`
- `function`
- `route`
- `site_id`
- `duration_ms`
- `call_count`
- `items_returned`
- `payload_bytes`
- `status`
- `cache_hit`
- `retry_count`
- `timestamp_utc`

## Acceptance criteria
- Route-level monitoring covers all API endpoints.
- Connection-level monitoring separates network time from local processing time.
- Site health processing can be broken into stage-level timings.
- Data can be measured at representative site sizes without altering user-visible behavior.
