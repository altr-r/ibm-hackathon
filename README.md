# Black Flag

**AI-assisted Linux portability engine.**  
Automatically discovers, adapts, and verifies selected Linux applications across Ubuntu, Fedora, and Arch Linux with a simple installation workflow.

```
black-flag analyze ./my-app
black-flag adapt   ./my-app
black-flag install portable-demo-0.1.0.bfpack
```

---

## What Black Flag Does

Given a Linux application repository, Black Flag:

1. **Analyzes** source code for distro-specific assumptions (AST-based for Python, regex for shell/C)
2. **Plans** portability adaptations using AI (local Ollama/Granite or IBM watsonx.ai) or deterministic rules
3. **Applies** reversible, bounded adaptations from a curated primitive catalog
4. **Builds and tests** the application in Docker containers for all three target distros
5. **Diagnoses** failures and iterates repairs (up to 3 iterations)
6. **Packages** the result as a `.bfpack` archive with manifest, diffs, and stage scripts
7. **Installs** the package natively on the target host (no VM, no container at runtime)

---

## Supported Targets

| Distribution   | Package Manager | Docker Image        |
|----------------|----------------|---------------------|
| Ubuntu 22.04   | apt-get        | ubuntu:22.04        |
| Fedora 41      | dnf            | fedora:41           |
| Arch Linux     | pacman         | archlinux:latest    |

**Architecture:** x86-64 only.

---

## Installation

```bash
pip install -e ".[dev]"        # development install with test deps
pip install -e ".[web]"        # web dashboard deps (FastAPI + uvicorn)
pip install -e ".[dev,web]"    # everything: tests + web dashboard
pip install .                  # production install (CLI only)
```

**Requirements:** Python 3.11+, Docker (for the build/test matrix). The optional
web dashboard needs FastAPI + uvicorn (installed via the `web` extra).

---

## Quick Start

```bash
# 1. See your environment
black-flag info

# 2. Analyze a project for portability issues
black-flag analyze examples/portable-demo/

# 3. Run the full adaptation loop (requires Docker)
black-flag adapt examples/portable-demo/

# 4. Install the resulting package
black-flag install portable-demo-0.1.0.bfpack

# 5. Check installed packages
black-flag status
```

---

## Running Black Flag Locally

The easiest way to demo Black Flag is the **local web dashboard** driven by a
local **Ollama (IBM Granite)** model — no cloud credentials required.

### 1. Python environment

```bash
python -m venv .venv
# Windows PowerShell:  .venv\Scripts\Activate.ps1
# macOS / Linux:       source .venv/bin/activate
pip install -e ".[dev,web]"
```

### 2. Install Ollama and pull Granite

Install Ollama from <https://ollama.com/download>, make sure it is running, then
pull the model Black Flag uses:

```bash
ollama pull granite4.2:3b
```

Ollama exposes a local HTTP API on `http://localhost:11434` by default.

### 3. Configure the AI provider

```bash
# Windows PowerShell
$env:AI_PROVIDER="ollama"
$env:OLLAMA_BASE_URL="http://localhost:11434"
$env:OLLAMA_MODEL="granite4.2:3b"

# macOS / Linux
export AI_PROVIDER=ollama
export OLLAMA_BASE_URL=http://localhost:11434
export OLLAMA_MODEL=granite4.2:3b
```

All three variables have sensible defaults, so `AI_PROVIDER=ollama` alone is
enough. No `WATSONX_*` credentials are needed for Ollama.

### 4. Start the web app

```bash
black-flag web                 # serves http://127.0.0.1:8000
# or directly:
uvicorn black_flag.web.app:app --host 127.0.0.1 --port 8000
```

### 5. Open the browser

Visit **http://127.0.0.1:8000**, then click **Launch Demo → Analyze Portability →
Adapt Project**. You will see the live pipeline (Analyze → AI Plan → Adapt →
Build → Test → Diagnose → Repair → Verify → Package), a terminal-style log, the
compatibility matrix, the before/after portability scores, and a **.bfpack**
download — all produced by the real engine.

### 6. CLI usage (same engine)

```bash
black-flag info                              # shows the active provider
black-flag analyze examples/portable-demo/
black-flag adapt   examples/portable-demo/   # honors AI_PROVIDER
black-flag adapt   examples/portable-demo/ --no-ai   # force deterministic
```

### Deterministic fallback

If Ollama is not running, the model is missing, a request times out, or the model
returns invalid/unsupported output, Black Flag **automatically falls back to the
deterministic rule-based provider** and never crashes. Both the CLI and the web
log report the real outcome (`Ollama request: SUCCESS` vs `FAILED … fallback:
deterministic`) so a fallback is never mistaken for a real AI response.

### watsonx.ai remains optional

Set `AI_PROVIDER=watsonx` (plus the `WATSONX_*` variables) to use IBM watsonx.ai
instead of local Ollama. See [AI Providers](#ai-providers).

---

## CLI Commands

```
black-flag analyze <dir>             Static analysis only; print issues and score
black-flag adapt   <dir>             Full adaptation loop → .bfpack
  --dry-run                          Analyze + plan + diffs; no Docker
  --no-ai                            Force deterministic provider
  --max-iterations N                 Repair loop limit (default: 3)
  --target ubuntu,fedora,arch        Override target list
  --output-dir DIR                   .bfpack output directory

black-flag install  <file.bfpack>    Install on host
  --force                            Reinstall if already installed

black-flag status   [name]           Show installed packages
black-flag info                      Environment info (provider, Docker, targets)
black-flag web                       Launch the local web dashboard (FastAPI)
  --host 127.0.0.1                   Bind host
  --port 8000                        Bind port
black-flag --version                 Show version
```

---

## AI Providers

Black Flag supports three interchangeable reasoning providers, selected with the
`AI_PROVIDER` environment variable (or the CLI `--no-ai` flag):

| `AI_PROVIDER`   | Provider                    | Credentials                 | Notes |
|-----------------|-----------------------------|-----------------------------|-------|
| `ollama`        | Local Ollama (IBM Granite)  | none                        | Default for local demos — `http://localhost:11434` |
| `watsonx`       | IBM watsonx.ai              | `WATSONX_API_KEY` + project | Cloud-hosted Granite |
| `deterministic` | Rule-based                  | none                        | Always-available fallback |

Whichever provider is active, the model is used for two **build-time** tasks
only — it is never used at application runtime:

- **Mode A — Adaptation planning:** given the detected portability issues, the
  target distros, and the primitive catalog, the model returns a strict-JSON plan
  selecting which bounded primitives to apply.
- **Mode B — Failure diagnosis:** when a Docker build/test stage fails, the model
  receives the distro, stage, stdout/stderr, and already-applied diffs, and
  returns a constrained repair decision (apply one primitive, or `give_up`).

The model **never executes shell commands and never rewrites files directly**.
Its output is limited to IDs from the 7-primitive catalog; every ID is validated
against the catalog and any invalid ID is dropped. All actual file modifications
are performed by the bounded, reversible primitive engine.

### Ollama (local, no credentials)

```bash
export AI_PROVIDER=ollama
export OLLAMA_BASE_URL=http://localhost:11434   # default
export OLLAMA_MODEL=granite4.2:3b               # default
```

Ollama runs entirely locally via `POST {OLLAMA_BASE_URL}/api/chat` and needs
**no IBM credentials** — it is the recommended provider for the hackathon demo.

### IBM watsonx.ai (optional)

Credentials are supplied **only** through environment variables — the API key is
never hardcoded, logged, or committed.

```bash
export WATSONX_API_KEY="your-ibm-cloud-api-key"       # IBM Cloud API key (secret)
export WATSONX_PROJECT_ID="your-watsonx-project-id"
export WATSONX_BASE_URL="https://us-south.ml.cloud.ibm.com/ml/v1"
export WATSONX_MODEL_ID="ibm/granite-3-3-8b-instruct"  # IBM Granite instruct model
```

> `WATSONX_BASE_URL` must match the region where your watsonx.ai project's
> service instance lives (e.g. `us-south`, `eu-de`, `jp-tok`). The project must
> be **associated with a watsonx.ai service instance**, otherwise the API returns
> `no_associated_service_instance_error`.

### Authentication

Black Flag implements the production IBM Cloud auth flow — it does **not** send
the raw API key as a bearer token:

1. The IBM Cloud API key (`WATSONX_API_KEY`) is exchanged for a short-lived
   **IAM access token** at `https://iam.cloud.ibm.com/identity/token`
   (`grant_type=urn:ibm:params:oauth:grant-type:apikey`).
2. The IAM access token is used as `Authorization: Bearer <token>` for the
   inference call to `POST {WATSONX_BASE_URL}/text/chat?version=2024-05-31`.
3. The token is cached in memory and automatically refreshed with a single
   retry on HTTP 401 (tokens expire). The token is never logged.

### Provider selection & fallback behavior

The provider is chosen by `AI_PROVIDER` (`ollama` / `watsonx` / `deterministic`).
When `AI_PROVIDER` is unset, Black Flag preserves its original default: it uses
watsonx if `WATSONX_API_KEY` is present, otherwise deterministic.

The **deterministic provider** (rule-based category→primitive mapping) remains
the always-available fallback. On any failure — Ollama not running, model
missing, no watsonx key, IAM error, timeout, non-200 response, malformed JSON,
or invalid/unsupported primitives — Black Flag safely falls back to
deterministic rules and never crashes.

The CLI and the web log both report the **real** outcome of each AI call so a
silent fallback is never mistaken for success:

```
AI provider: ollama  (model: granite4.2:3b)
  Ollama request: SUCCESS (Mode A: planning) — response generated by ollama
```
or
```
  Watsonx request: FAILED (http-403:no_associated_service_instance_error) — fallback: deterministic
```

`black-flag info` shows the configured provider, model, and endpoint without
revealing any credential.

> watsonx.ai is a **build-time / development-time** reasoning component only.
> The packaged application and its runtime never depend on watsonx.

---

## Adaptation Primitive Catalog

Black Flag applies bounded, reversible transformations from a 7-primitive catalog:

| ID | Applies To | What It Fixes |
|----|-----------|---------------|
| `shell_compat` | `.sh` | `#!/bin/bash` shebang, `[[ ]]` bash-isms |
| `pkg_manager_call` | `.sh` | `apt-get` → distro-dispatch case block |
| `distro_path_check` | `.py` | `/etc/debian_version` → `/etc/os-release` |
| `env_var_portability` | `.py` | `os.environ["DEBIAN_FRONTEND"]` → `.get()` |
| `package_name_remap` | `.sh` | Debian pkg names → generic |
| `requirements_normalize` | `.txt` | `python3-cryptography` → `cryptography` |
| `service_name_remap` | `.sh` | `apache2` → `httpd` on Fedora |

Applications requiring transformations outside this catalog are reported **UNSUPPORTED** for the affected target, with the exact build/test failure included.

---

## Portability Score

Score is computed deterministically from detected issues — AI output does not affect it:

```
score = 1.0 - weighted_penalty / max(weighted_penalty, 5.0)

severity weights:  error=1.0  warning=0.4  info=0.1
target weights:    1 target=0.5  2 targets=0.75  3 targets=1.0
```

| Range | Indicator | Meaning |
|-------|-----------|---------|
| 0.90–1.00 | ✅ | Portable |
| 0.70–0.90 | 🟢 | Mostly portable |
| 0.40–0.70 | 🟡 | Partially portable |
| 0.00–0.40 | 🔴 | Not portable |

---

## Demo Application

`examples/portable-demo/` contains a small Python/shell app with **four deliberate portability flaws**:

| Flaw | File | Problem | Primitive |
|------|------|---------|-----------|
| 1 | `setup.sh:1` | `#!/bin/bash` shebang | `shell_compat` |
| 2 | `setup.sh:14` | `apt-get install -y libssl-dev` | `pkg_manager_call` |
| 3 | `app.py:24` | `os.path.exists("/etc/debian_version")` | `distro_path_check` |
| 4 | `app.py:35` | `os.environ["DEBIAN_FRONTEND"]` | `env_var_portability` |

**Before adaptation:**
```
Ubuntu ✅   Fedora ❌ (apt-get not found)   Arch ❌ (apt-get not found)
```

**After Black Flag adapt (1 iteration):**
```
Ubuntu ✅   Fedora ✅   Arch ✅
```

Run the demo:
```bash
bash scripts/pull-images.sh          # pre-pull Docker images (~2 GB)
black-flag adapt examples/portable-demo/
```

---

## Running Tests

```bash
pytest                               # all tests
pytest tests/test_phase1.py -v       # Phase 1 (types, adapters, detector)
pytest tests/test_analyzer.py -v     # Phase 2 (analyzer, score)
pytest tests/test_primitives.py -v   # Phase 2 (all 7 primitives)
pytest tests/test_adaptation_engine.py -v  # Phase 3 (patcher, planner, AI)
pytest tests/test_watsonx.py -v      # watsonx.ai provider (mocked HTTP)
pytest tests/test_ollama.py -v       # Ollama / Granite provider (mocked HTTP)
pytest tests/test_web.py -v          # web API (FastAPI TestClient)
```

---

## Known Limitations

Black Flag is a 48-hour hackathon prototype. The following limitations are by design.

**Portability envelope:** 7 adaptation primitives. Applications requiring transformations outside this set are reported UNSUPPORTED with the exact failure output.

**Adaptation scope:** Automatic modification is limited to `.sh`, `.py`, and `requirements.txt`. C/C++ source files, Makefiles, and binary artifacts are not modified.

**Static analysis depth:** Python analysis uses the AST module. C analysis is regex-based. Neither performs type inference, import tracing, or cross-file data-flow analysis.

**Build model:** `.bfpack` does not contain pre-compiled binaries. The application is built from patched source at install time.

**Package normalization:** The normalization table covers ~30 commonly used packages. Unknown packages pass through unchanged.

**Architecture:** x86-64 only. No ARM, RISC-V, Windows, or macOS support.

**Scope:** Black Flag does not claim to make *arbitrary* software portable. The claim is: *"Black Flag can automatically discover, adapt, and verify selected Linux software across Ubuntu, Fedora, and Arch with a simple installation workflow."*

---

## Project Structure

```
black_flag/
  core/           types.py, manifest.py, cache.py
  analyzer/       python_analyzer.py, shell_analyzer.py, c_analyzer.py, runner.py
  adapters/       base.py, ubuntu.py, fedora.py, arch.py, normalization.py
  primitives/     catalog.py + 7 primitive modules
  adaptation_engine/  patcher.py, rollback.py, planner.py
  ai/             provider.py, deterministic.py, watsonx.py, ollama.py, factory.py, prompts.py
  build/          container.py, script_gen.py, matrix.py
  packager/       packer.py
  installer/      installer.py
  runtime/        detector.py
  cli/            main.py
  web/            app.py, routes.py, schemas.py, services.py, static/ (SPA)
examples/
  portable-demo/  app.py, setup.sh, requirements.txt, test.sh
scripts/          pull-images.sh
tests/            test_phase1.py, test_analyzer.py, test_primitives.py, test_adaptation_engine.py, test_watsonx.py, test_ollama.py, test_web.py
docs/             architecture.md
```

---

## Bob Development Log

See [`BOB_DEVELOPMENT_LOG.md`](BOB_DEVELOPMENT_LOG.md) for how IBM Bob was used throughout development.
