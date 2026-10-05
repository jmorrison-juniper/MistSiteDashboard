# MistSiteDashboard agent instructions

This file holds the rules that apply to MistSiteDashboard only. The rules that apply to each
repository of this owner are in `AGENTS.md` at the repository root. Read `AGENTS.md` first. This
file adds to it, and it does not hold a copy of a rule from it. Where the two files disagree, obey
`AGENTS.md` for a writing rule, a safety rule, or a security rule.

## What this repository is

MistSiteDashboard is a Python 3.13 Flask dashboard for the Juniper Mist Cloud. It shows health
scores for organizations and sites, client history, and Service Level Experience (SLE).
Run it with Docker, Podman, or Python.

## Language and environment

Use Python 3.13. Make a virtual environment, then install the development requirements:

```sh
python3.13 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements-dev.txt
```

The container uses Python 3.13. The project uses pip and keeps runtime requirements in
`requirements.txt`.
On Windows, activate the environment with `.venv\Scripts\Activate.ps1`.

## Local gates

Run each command from the repository root. The expected result is a zero exit code.

| Gate | Command | Expected result |
| - | - | - |
| Syntax | `python -m py_compile app.py mist_connection.py perf_monitor.py` | All modules compile |
| Lint | `ruff check .` | No findings |
| Format | `black --check .` | No files need changes |
| Types | `mypy .` | No type errors |
| Tests and documentation | `python -m pytest` | All tests pass |
| Security | `bandit -q -r . -ll` | No high-severity findings |
| Dependency audit | `pip-audit -r requirements.txt` | No known runtime dependency issues |
| Dead code | `vulture . --min-confidence 90` | No findings |
| Complexity | `radon cc . -j \| complexity-gate --max 15` | No function exceeds 15 |
| STE | `ste-linter --config .ste-linter.toml --min-score 80 README.md AGENTS.md .github/copilot-instructions.md` | Each file scores 80 or above |

The CI workflow also runs the quality gates. The pytest suite checks the documentation.
Run `actionlint` when your environment has it.

## Architecture and conventions

| Path | Purpose |
| - | - |
| `app.py` | Flask pages and JSON API endpoints |
| `mist_connection.py` | Mist API calls and dashboard data |
| `perf_monitor.py` | Performance events for pages and API calls |
| `templates/` | HTML pages |
| `scripts/` | Offline demo, screenshot capture, and request benchmark |
| `tests/` | Pytest checks for docs, Mist data, and performance monitoring |

Before parallel work, agree on one owner for edits to `app.py`, `mist_connection.py`,
`perf_monitor.py`, and workflow files.

## Safety in this repository

The dashboard reads Mist data. It does not reboot devices, upgrade firmware, or change device
configuration. It accepts `MIST_APITOKEN` and optional `MIST_ORG_ID` from the environment. Keep
these values out of test output. The container writes logs to `/config/logs/app.log` when that
directory is writable.

The offline demo uses synthetic data and binds to `127.0.0.1`. The benchmark script uses a fake
Mist connection. Do not replace these test fixtures with a live connection.

## Containers and ports

The Compose entry and fixed container name are `mistsitedashboard`. The local stack publishes host
port `5000` for the Flask app. Tests do not start containers. Stop a manually started stack with
`docker compose down` or `podman compose down`. The repository defines no test container
name or free port range.

## Git and GitHub in this repository

Use the `documentation` label for documentation work and the `ci` label for workflow work. Use the
`in-progress` label while an issue is active. The repository has no scope labels, pull request
template, or `auto-merge` label.

The changelog is in `docs/user-guide.md`. Add a new entry with the `YY.MM.DD.HH.MM` UTC format.
The container image uses that format for its version tag. The CI workflow runs the Python quality
gates. The container workflow builds images for app changes and pull requests. The STE workflow
checks `README.md`, `AGENTS.md`, and `.github/copilot-instructions.md`. The CodeQL workflow
scans the Python code on each pull request, on each push to `main`, and each Monday.

The required checks for a pull request into `main` include `CodeQL` and
`codeql / Analyze (python)`. The CodeQL workflow does not cancel a `main` run.

## Known pitfalls

- Set `type="all"` in `listSiteDevicesStats` to include access points, switches, and gateways.
  Without this parameter, the Mist SDK returns access points only.
- The Mist SDK paginates device results. Use `mistapi.get_all()` when a request can return more
  than one page.
- The offline demo defaults to port `5055` and binds to loopback. It does not use the container port.

## Key files

| File | Purpose |
| - | - |
| `app.py` | Flask application and routes |
| `mist_connection.py` | Mist API access and data preparation |
| `perf_monitor.py` | Performance event collection |
| `requirements.txt` | Runtime dependencies |
| `requirements-dev.txt` | Development and CI tools |
| `docs/user-guide.md` | User guide and changelog |
| `.github/workflows/ci.yml` | Shared Python quality gates |
| `.github/workflows/container-build.yml` | Container image workflow |
| `.github/workflows/codeql.yml` | CodeQL analysis of the Python code |
| `.github/codeql/codeql-config.yml` | CodeQL configuration |

## External resources

Read `docs/user-guide.md` for setup, configuration, and Mist API endpoints. The project uses
the [mistapi Python SDK](https://github.com/tmunzer/mistapi_python).
