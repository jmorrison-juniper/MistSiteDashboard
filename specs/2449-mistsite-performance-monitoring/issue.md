# Issue: MistSiteDashboard performance monitoring instrumentation

## Summary
Add a thin, consistent performance-monitoring layer to the Flask dashboard and Mist API client so that all request paths and external API calls can be measured by latency, call count, payload size, and memory profile.

## Scope
- Measure request latency for each dashboard route.
- Measure the full MistConnection call stack, including session creation, retries, and per-endpoint fetch fan-out.
- Track payload sizes, row counts, and output serialization cost.
- Keep instrumentation additive, low-overhead, and compatible with local and test execution.

## Deliverables
- plan.md: route and connection instrumentation strategy
- research.md: source findings and monitoring map
- tasks.md: workstreams and acceptance criteria

## Primary hotspots
- `app.py` route handlers
- `mist_connection.py` connection and fetch orchestration
- all per-site API data fetch methods (`get_site_health`, `get_gateway_wan_status`, etc.)

## Success criteria
- Each relevant route emits a consistent timing event.
- The Mist API layer can distinguish network time from JSON-processing time.
- The team can benchmark request latency and object churn for each site data path.
