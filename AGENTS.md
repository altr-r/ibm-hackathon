# AGENTS.md

This file provides guidance to agents when working with code in this repository.

## Build / Test Commands

```bash
pip install -e ".[dev]"                          # install with dev deps
pytest                                           # run all tests
pytest tests/test_phase1.py -v                  # Phase 1 unit tests
pytest tests/test_analyzer.py -v                # analyzer + score formula
pytest tests/test_primitives.py -v              # all 7 primitives
pytest tests/test_adaptation_engine.py -v       # patcher, planner, AI providers
pytest tests/test_adaptation_engine.py::test_patcher_apply_primitive_shell_compat  # single test

black-flag info                                  # check environment
black-flag analyze examples/portable-demo/      # static analysis smoke test
black-flag adapt examples/portable-demo/ --dry-run --no-ai   # full loop without Docker
```

## Non-Obvious Project Conventions

### Primitive pattern: never partial-apply
Every `AdaptationPrimitive.apply()` must raise `PrimitiveNotApplicable` (from `black_flag/primitives/base.py`) if its pattern is not found. **Never silently return unchanged content** — the patcher uses this exception to detect and skip non-applicable primitives.

### Package name resolution is two-step
`resolve_package()` in `black_flag/adapters/normalization.py` uses **generic keys** (`ssl-dev`, not `libssl-dev`). `pkg_manager.py` has a reverse map `_UBUNTU_TO_GENERIC` to handle Debian-specific names found in install commands. When adding new packages to the normalization table, use the generic key as the table key, not the Debian/Ubuntu name.

### AI providers always fall back silently
`WatsonxProvider` catches all exceptions from HTTP calls and JSON parse failures, returning the `DeterministicProvider` result instead. **Never let an AI provider raise** — the calling code does not handle provider exceptions.

### Temp files must be read inside the `with` block on Windows
`tempfile.TemporaryDirectory()` deletes on exit. Tests that create files in a temp dir must read/assert inside the `with` block or use `delete=False`.

### Shell analyzer double-bracket pattern
`_RE_BASH_DBL_BRACKET` in `shell_analyzer.py` matches `[[` **anywhere** on a line (not just at line start). The original line-anchored version missed `if [[ -z ... ]]` patterns.

### All distro-specific behavior goes in adapters only
`black_flag/adapters/` contains all distro-specific commands. Never hard-code `apt-get`, `dnf`, or `pacman` outside an adapter or a generated stage script. Use `DistroAdapter.install_dependency()` / `normalize_library_name()` for runtime behavior.

### Stage scripts are always regenerated before matrix runs
`build/script_gen.py:generate_scripts()` is called at the start of every Docker matrix iteration, not just once. This ensures changes to `requirements.txt` from PRIM-006 are reflected in the prepare script.

### Portability score is deterministic
`analyzer/runner.py:compute_score()` is called only on the `PortabilityIssue` list. AI output does not affect the score. Tests that assert score values must check against the formula: `1.0 - penalty / max(penalty, 5.0)`.

### Manifest JSON round-trip
`core/manifest.py` has both `manifest_to_dict()` and `manifest_from_dict()`. All `TargetResult.status` fields are string literals from `TargetStatus` — valid values are `"COMPATIBLE"`, `"FAILED"`, `"UNSUPPORTED"`, `"UNVERIFIED"`.

### Entry point
Defined in `pyproject.toml`: `black-flag = "black_flag.cli.main:app"`. All CLI commands are in `black_flag/cli/main.py`.

### Docker images
- Ubuntu: `ubuntu:22.04`
- Fedora: `fedora:41` (not 39 — reached EOL Nov 2024)
- Arch: `archlinux:latest` — **requires `pacman -Sy archlinux-keyring --noconfirm` before any other `pacman` call** (baked into `_ARCH_PREPARE` in `build/script_gen.py`)

### `.bfpack` format
A gzip-compressed tar archive. Everything is under the `source/` prefix inside the archive. The installer strips this prefix when extracting to `~/.local/share/bfpack/<name>/`.
