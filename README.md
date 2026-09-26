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
2. **Plans** portability adaptations using AI (IBM watsonx.ai) or deterministic rules
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
pip install -e ".[dev]"       # development install with test deps
pip install .                  # production install
```

**Requirements:** Python 3.11+, Docker (for build/test matrix)

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
black-flag info                      Environment info
black-flag --version                 Show version
```

---

## AI Integration (IBM watsonx.ai)

Set these environment variables to enable AI-assisted planning:

```bash
export WATSONX_API_KEY="your-ibm-cloud-api-key"
export WATSONX_BASE_URL="https://us-south.ml.cloud.ibm.com/ml/v1"
export WATSONX_MODEL_ID="ibm/granite-3-8b-instruct"
export WATSONX_PROJECT_ID="your-project-id"
```

Without these variables, Black Flag uses its deterministic provider (rule-based category→primitive mapping). The output format is identical — the deterministic provider covers the demo application completely.

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
  ai/             provider.py, deterministic.py, watsonx.py, factory.py, prompts.py
  build/          container.py, script_gen.py, matrix.py
  packager/       packer.py
  installer/      installer.py
  runtime/        detector.py
  cli/            main.py
examples/
  portable-demo/  app.py, setup.sh, requirements.txt, test.sh
scripts/          pull-images.sh
tests/            test_phase1.py, test_analyzer.py, test_primitives.py, test_adaptation_engine.py
docs/             architecture.md
```

---

## Bob Development Log

See [`BOB_DEVELOPMENT_LOG.md`](BOB_DEVELOPMENT_LOG.md) for how IBM Bob was used throughout development.
