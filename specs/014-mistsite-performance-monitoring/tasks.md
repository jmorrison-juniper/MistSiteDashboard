# MistSiteDashboard performance monitoring tasks

## Task 1: Add request-level timing wrapper
- Wrap Flask route handlers with a standard latency monitor.
- Record duration, status, payload size, and route name.
- Acceptance: every dashboard route emits a consistent timing event.

## Task 2: Add connection-level timing
- Instrument `MistConnection` session creation and API fetch paths.
- Record call count, response size, and slow operation markers.
- Acceptance: network time is distinguishable from processing time.

## Task 3: Break down site health profiling
- Separate `get_site_health` stages into template fetch, mapping, device stats fetch, and summary generation.
- Acceptance: each stage can be compared independently.

## Task 4: Add allocation tracing for large site responses
- Run `tracemalloc` and sampling on large list-heavy responses.
- Acceptance: churn hotspots are identifiable by function and row count.

## Task 5: Add benchmark harness for dashboard routes
- Run representative requests against small and large site payloads.
- Capture p50/p95 duration and response size.
- Acceptance: route latency is known before optimization begins.

## Task 6: Finalize telemetry schema
- Normalize fields across route and connection monitors.
- Acceptance: all new events follow one schema and can be aggregated by route or function.
