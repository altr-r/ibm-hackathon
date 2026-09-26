# Project BLACK FLAG — Revised Architecture & 48-Hour Implementation Plan

> **Version:** 2.0 (revised from static-analysis prototype to full adaptation engine)
>
> **Mission:** An AI-assisted compatibility engine that transforms a Linux application
> into a verified portable package that installs and runs correctly on Ubuntu 22.04,
> Fedora 39, and Arch Linux (x86-64), or reports exactly why each target fails.

---

## Top-Level Overview

Black Flag operates a closed feedback loop:

```
Analyze → Plan → Adapt → Build → Test → Diagnose → Repair → Build → Test → Verify → Package
```

Given a source application directory, it:
1. Statically analyzes code for portability issues
2. Uses AI to select applicable adaptation primitives from a curated catalog
3. Applies those primitives as reversible patches to a working tree copy
4. Builds and runs the application inside Docker containers for all three target distros
5. If any target fails, uses AI to diagnose the failure from real stdout/stderr and apply a repair primitive
6. Iterates (up to 3 times) until all targets pass or failures are declared UNSUPPORTED
7. Packages the patched source tree, applied diffs, and compatibility manifest as a `.bfpack`
8. Installs from `.bfpack` by building from patched source on the host at install time

**AI participates in:** portability analysis, adaptation planning, code/config modification,
build failure diagnosis, and repair selection.

**AI does NOT:** run at application runtime, generate arbitrary code, or operate outside
the bounded primitive catalog.

The output is verifiable: every applied adaptation has a diff, a reason, and proof that
all three distros passed PREPARE + BUILD + TEST.

---

## 1. Technical Risk Assessment

### What is feasible in 48 hours
- Static analysis (Python AST + regex for shell/C): feasible
- 7-primitive catalog with deterministic patch application: feasible
- AI planning via structured JSON prompt (watsonx.ai / OpenAI-compatible): feasible
- Deterministic AI fallback (pattern-matching): feasible
- Docker 3-stage × 3-distro matrix via `subprocess`: feasible with pre-pulled images
- Repair loop with max 3 iterations: feasible (~7 min worst case)
- `.bfpack` as tar.gz (patched source + diffs + manifest): feasible
- Installer that builds from source at install time: feasible for Python apps
- CLI with `typer` + `rich` progress output: feasible
- Compatibility cache on disk: feasible (JSON keyed by source sha256)

### What is NOT feasible in 48 hours — explicitly cut
- Arbitrary source code rewriting (anything beyond the 7 primitives)
- C project adaptation (modifying Makefiles, CMakeLists, headers)
- Pre-compiled binary packages (cross-compilation setup is 8h+ alone)
- Comprehensive package normalization (1000+ packages) — table covers ~30
- `.bfpack` signature verification
- `textual` TUI dashboard — `rich` tables are the baseline; TUI is P1
- Windows / macOS / ARM / RISC-V

### What would make this "just a static analyzer" (guard rails)
The plan is NOT just a static analyzer if:
- It applies patches to actual files (patcher.py runs, diffs are written)
- It runs real Docker build+test (not just install scripts)
- It feeds real failure output back to AI for repair selection
- The before/after matrix result changes (failures become passes)
- The `.bfpack` contains the patched source, not the original

If any of these four conditions fails, the product regresses to a report generator.
These are P0 requirements, not P1.

### Hidden dependencies and risks
- Docker must be running; graceful `--dry-run` mode if absent
- Arch `archlinux:latest` needs `pacman -Sy archlinux-keyring --noconfirm` first (~2-3 min)
  → Mitigation: pre-pull + bake keyring step into `prepare.sh`
- AI response must be structured JSON for primitive selection; free text is not parseable
  → Mitigation: prompt forces JSON output format; fallback to deterministic on parse failure
- `patcher.py` must not corrupt files on partial matches
  → Mitigation: primitives use exact pattern matching; skip (don't partially apply) if pattern not found
- Repair loop can make things worse
  → Mitigation: track "best iteration" state; roll back before applying repair

---

## 2. Architecture Decision Records

### ADR-001: Language → Python 3.11+
**Decision:** Python
**Rationale:** `ast`, `subprocess`, `tarfile`, `json`, `difflib`, `pathlib`, `hashlib` cover every
requirement with zero external setup. Go/Rust scaffolding costs 4–6h in a 48h window. Python ships
inside all three target container images. `typer`, `rich`, `httpx` are battle-tested.
**Rejected:** Go (faster binary, slower to scaffold); Rust (best performance, not feasible in 48h)

### ADR-002: Dashboard → `rich` tables (P0) + `textual` TUI (P1)
**Decision:** Baseline is `rich` live tables and progress output in the terminal.
`textual` TUI (`black-flag dashboard`) is P1.
**Rationale:** React requires Node.js + build pipeline. Textual is pure Python but takes ~3h.
The core product value is the adaptation loop, not the UI.

### ADR-003: AI Integration → Structured JSON prompt to OpenAI-compatible endpoint
**Decision:** `httpx.post` to `/v1/chat/completions`; prompt demands JSON output conforming to
`AdaptationPlan` or `RepairAction` schema. Falls back to `DeterministicProvider` on missing
API key, network failure, or JSON parse failure.
**Prompt contract:** Mode A (planning) returns `{"primitives": [{primitive_id, params, issue_ids, rationale}]}`.
Mode B (diagnosis) returns `{"primitive_id": "...", "params": {...}, "rationale": "..."}` or `{"action": "give_up", "reason": "..."}`.
**Rejected:** watsonx SDK (adds setup time); free-text AI response (not machine-parseable)

### ADR-004: Primitive Application → Direct string replacement, diff generated afterward
**Decision:** Primitives perform direct string/AST replacement on file content. A unified diff
is generated *after* the fact (before vs. after) using `difflib`. Diffs are stored as records,
not applied from diffs (avoids patch application failures on fuzzy matches).
**Rejected:** Apply-from-diff approach (fragile on varying whitespace and line endings)

### ADR-005: Container Strategy → `docker run --rm -v` via subprocess, 3 stages
**Decision:** For each (distro, stage) pair: write the stage script into the working tree,
mount the working tree as `/src`, run the script, capture all output.
```
docker run --rm -v <working_tree>:/src <image> bash /src/bf_scripts/<distro>/<stage>.sh
```
No custom Dockerfile builds in the test harness.
**Images:** `ubuntu:22.04`, `fedora:41`, `archlinux:latest`

### ADR-006: `.bfpack` Contents → Patched source + diffs + manifest + scripts
**Decision:** `.bfpack` contains the patched working tree, all applied diffs, and pre-generated
`bf_scripts/` per distro. Installation builds from patched source on the host.
**Rejected:** Pre-built binaries (requires cross-compilation, 8h+); original source only (defeats the purpose)

### ADR-007: Manifest Format → JSON
**Decision:** stdlib `json`. No YAML dependency.

### ADR-008: Working Tree → Copy in temp dir, no Git dependency
**Decision:** Copy source dir to a temp dir at the start of `adapt`. Apply all patches in-place.
Track changes in-memory as `list[AppliedDiff]`. Rollback = re-copy from original source.
**Rejected:** Git working tree (requires Git installed; adds subprocess complexity; overkill for 48h)
**Rollback mechanism:** On iteration failure, wipe temp dir and re-copy from original, then
re-apply only the diffs from the best-so-far state.

---

## 3. Component Architecture

### Directory Layout

```
black-flag/
  black_flag/
    core/
      types.py            — all shared dataclasses and enums
      manifest.py         — CompatibilityManifest builder + score formula
      cache.py            — compatibility cache (JSON on disk, sha256 key)
    analyzer/
      runner.py           — orchestrate analyzers, return list[PortabilityIssue]
      python_analyzer.py  — ast.walk: subprocess, os.path, env vars
      shell_analyzer.py   — regex: pkg manager calls, bash-isms
      c_analyzer.py       — regex: system(), popen(), Debian paths
    primitives/
      catalog.py          — PrimitiveCatalog: loads all primitives by ID
      base.py             — AdaptationPrimitive ABC
      pkg_manager.py      — PRIM-001: apt↔dnf↔pacman dispatch block
      path_normalize.py   — PRIM-002: /etc/debian_version → _bf_detect_distro()
      env_portability.py  — PRIM-003: os.environ["KEY"] → os.environ.get("KEY", default)
      shell_compat.py     — PRIM-004: shebang normalization + [[ conversion
      pkg_name_remap.py   — PRIM-005: package name substitution in shell scripts
      requirements_norm.py — PRIM-006: requirements.txt normalization
      service_names.py    — PRIM-007: Debian service name → portable
    adaptation_engine/
      planner.py          — receives AdaptationPlan from AI, validates against catalog
      patcher.py          — applies primitives to working tree files; records AppliedDiff
      rollback.py         — reverts working tree to a prior state
      diff_record.py      — AppliedDiff dataclass
    build/
      matrix.py           — orchestrate 3-stage × 3-distro matrix; return BuildMatrix
      container.py        — docker run abstraction with availability check
      script_gen.py       — write prepare.sh / build.sh / test.sh per distro
      result.py           — StageResult, BuildResult, BuildMatrix dataclasses
    ai/
      provider.py         — AIProvider ABC
      deterministic.py    — DeterministicProvider: issue→primitive rules; stderr→repair rules
      watsonx.py          — WatsonxProvider: httpx POST, JSON response parsing
      factory.py          — return provider based on env vars
      prompts.py          — Mode A (planning) and Mode B (diagnosis) prompt templates
    packager/
      packer.py           — create .bfpack tar.gz from working tree + manifest + diffs
    installer/
      installer.py        — extract .bfpack, detect distro, run prepare+build, symlink
    runtime/
      detector.py         — parse /etc/os-release, return active DistroAdapter
    adapters/
      base.py             — DistroAdapter ABC (8 methods)
      ubuntu.py           — AptAdapter
      fedora.py           — DnfAdapter
      arch.py             — PacmanAdapter
      normalization.py    — 30-package generic→distro table
    cli/
      main.py             — typer app: adapt / analyze / install / status
    dashboard/
      app.py              — rich/textual output (P1: textual TUI)
  examples/
    portable-demo/
      app.py              — 4 deliberate portability flaws
      setup.sh            — 2 deliberate portability flaws
      requirements.txt
      test.sh
      README.md           — explains each flaw
  scripts/
    pull-images.sh        — docker pull ubuntu:22.04 fedora:41 archlinux:latest
  tests/
    test_analyzer.py
    test_primitives.py
    test_score.py
    test_loop.py
  docs/
    architecture.md
  pyproject.toml
  README.md
```

### Key Interfaces

#### `PortabilityIssue` (core/types.py)
```python
@dataclass
class PortabilityIssue:
    category: str          # "package-manager" | "hardcoded-path" | "library-name"
                           # "shell-ism" | "env-assumption" | "service-name"
    severity: str          # "error" | "warning" | "info"
    source_file: str       # relative path from source root
    line: int              # 1-based; 0 = file-level
    affected_targets: list[str]   # subset of ["ubuntu", "fedora", "arch"]
    explanation: str
    suggested_primitive: str | None   # hint for planner; AI may override
    verification_method: str          # "docker-test" | "static" | "manual"
```

#### `AdaptationPlan` (core/types.py)
```python
@dataclass
class PrimitiveApplication:
    primitive_id: str         # "pkg_manager_call", "distro_path_check", etc.
    params: dict              # primitive-specific parameters
    issue_ids: list[int]      # indices into issues list this primitive addresses
    rationale: str            # AI-provided or deterministic explanation

@dataclass
class AdaptationPlan:
    applications: list[PrimitiveApplication]   # ordered by dependency
    source: str               # "ai" | "deterministic"
```

#### `AppliedDiff` (adaptation_engine/diff_record.py)
```python
@dataclass
class AppliedDiff:
    sequence: int             # 001, 002, ...
    primitive_id: str
    params: dict
    target_file: str          # relative path
    diff_text: str            # unified diff (before vs after)
    reason: str
    affected_targets: list[str]
    verification_method: str
    iteration: int            # which repair loop iteration
```

#### `StageResult` / `BuildMatrix` (build/result.py)
```python
@dataclass
class StageResult:
    distro: str               # "ubuntu" | "fedora" | "arch"
    stage: str                # "prepare" | "build" | "test"
    exit_code: int
    stdout: str
    stderr: str
    elapsed_s: float

@dataclass
class BuildMatrix:
    results: list[StageResult]
    iteration: int

    def passed(self, distro: str) -> bool:
        return all(r.exit_code == 0 for r in self.results if r.distro == distro)

    def all_passed(self) -> bool:
        return all(r.exit_code == 0 for r in self.results)

    def failed_results(self) -> list[StageResult]:
        return [r for r in self.results if r.exit_code != 0]
```

#### `DistroAdapter` ABC (adapters/base.py)
```python
class DistroAdapter(ABC):
    @abstractmethod
    def detect(self) -> bool: ...              # matches running distro?
    @abstractmethod
    def package_manager(self) -> str: ...      # "apt" | "dnf" | "pacman"
    @abstractmethod
    def install_dependency(self, pkg: str) -> str: ...   # shell command
    @abstractmethod
    def query_package(self, pkg: str) -> bool: ...       # in normalization table?
    @abstractmethod
    def normalize_library_name(self, generic: str) -> str: ...
    @abstractmethod
    def environment_configuration(self) -> dict: ...     # env vars to inject
    @abstractmethod
    def verify_dependency(self, pkg: str) -> str: ...    # verify shell command
    @abstractmethod
    def uninstall_dependency(self, pkg: str) -> str: ... # uninstall shell command
```

#### `AdaptationPrimitive` ABC (primitives/base.py)
```python
class AdaptationPrimitive(ABC):
    id: str                    # unique slug, e.g. "pkg_manager_call"
    description: str
    supported_file_types: list[str]   # [".sh", ".py", ".c"]
    safe_to_auto_apply: bool   # False = require --force flag

    @abstractmethod
    def matches(self, file_content: str, file_ext: str) -> bool: ...
    # Returns True if this primitive can be applied to the given content

    @abstractmethod
    def apply(self, file_content: str, params: dict, adapters: dict) -> str: ...
    # Returns modified file content; raises PrimitiveNotApplicable if pattern not found
```

#### `AIProvider` ABC (ai/provider.py)
```python
class AIProvider(ABC):
    @abstractmethod
    def plan_adaptations(
        self,
        issues: list[PortabilityIssue],
        catalog: list[str]          # available primitive IDs
    ) -> AdaptationPlan: ...
    # Mode A: given issues and catalog, return ordered plan

    @abstractmethod
    def diagnose_failure(
        self,
        failed_result: StageResult,
        applied_diffs: list[AppliedDiff],
        catalog: list[str]
    ) -> RepairAction: ...
    # Mode B: given failure output and what's been tried, return repair or give_up
```

### Module Dependency Graph

```
core ◄──── analyzer
core ◄──── primitives ◄──── adapters
core ◄──── adaptation_engine ──► primitives
core ◄──── build ──► adapters
core ◄──── ai
core ◄──── packager ──► adaptation_engine, build
core ◄──── installer ──► adapters, runtime
cli ────► everything (thin orchestration layer)
dashboard ──► core
```

---

## 4. Safe Adaptation Primitives Catalog

A primitive is a named, parameterized, reversible, bounded transformation. The AI selects
primitives by ID from this catalog. It never generates code outside this catalog in P0.

Every primitive: detects whether it is applicable before applying; raises `PrimitiveNotApplicable`
if the pattern is not found (never silently partially applies); produces a before/after diff.

### PRIM-001: `pkg_manager_call`
**Applies to:** `.sh` files
**Detects:** Lines calling `apt-get`, `apt install`, `yum`, `dnf install`, `pacman -S` directly
**Transforms:** Replaces with distro-dispatch case block
**Params:** `{generic_name: "ssl-dev", files: ["setup.sh"]}`
**Output example:**
```bash
_BF_DISTRO=$(. /etc/os-release && echo "$ID")
case "$_BF_DISTRO" in
  ubuntu|debian) apt-get install -y libssl-dev python3-cryptography ;;
  fedora|rhel)   dnf install -y openssl-devel python3-cryptography ;;
  arch)          pacman -S --noconfirm openssl python-cryptography ;;
  *) echo "Unsupported distro: $_BF_DISTRO" && exit 1 ;;
esac
```
**Safety:** Only replaces the matched line(s). Does not alter surrounding logic.

### PRIM-002: `distro_path_check`
**Applies to:** `.py` files
**Detects:** `os.path.exists("/etc/debian_version")` or `open("/etc/debian_version")`
**Transforms:** Injects `_bf_detect_distro()` helper at top of file; replaces path check
with a call to `_bf_detect_distro()`
**Params:** `{original_path: "/etc/debian_version"}`
**Safety:** Only applied when the path is used in a simple boolean existence test.
If the file is opened and read (not just checked), the primitive rewrites to read `/etc/os-release` instead.

### PRIM-003: `env_var_portability`
**Applies to:** `.py` files
**Detects:** `os.environ["DEBIAN_FRONTEND"]` or other `os.environ["DEBIAN_*"]` patterns
**Transforms:** `os.environ["KEY"]` → `os.environ.get("KEY", "noninteractive")`
**Params:** `{var_name: "DEBIAN_FRONTEND", default: "noninteractive"}`
**Safety:** Only transforms direct subscript access. Does not alter downstream logic
that branches on the value (flagged as WARNING for manual review).

### PRIM-004: `shell_compat`
**Applies to:** `.sh` files
**Detects:** `#!/bin/bash` shebangs, `[[ ]]` test syntax, `$BASH_VERSION` references
**Transforms (strategy: `explicit_bash`):** Changes `#!/bin/bash` → `#!/usr/bin/env bash`
(declares explicit bash dependency rather than assuming `/bin/bash` location)
**Transforms (strategy: `posix_convert`):** Converts `[[ -z "$X" ]]` → `[ -z "$X" ]`
where the test is a simple string/existence check
**Params:** `{strategy: "explicit_bash" | "posix_convert", file: "setup.sh"}`
**Safety:** `posix_convert` only for `-z`, `-n`, `-f`, `-d`, `-e` tests. Arithmetic
and regex tests remain as `explicit_bash`.

### PRIM-005: `package_name_remap`
**Applies to:** `.sh`, `requirements.txt`, inline comments
**Detects:** Known Debian-specific package names in install commands or `# REQUIRES:` comments
**Transforms:** Replaces Debian name with the correct distro-specific name per adapter table
**Params:** `{debian_name: "libssl-dev", generic_name: "ssl-dev"}`

### PRIM-006: `requirements_normalize`
**Applies to:** `requirements.txt`
**Detects:** Entries like `python3-cryptography` (Debian package name, not PyPI name)
**Transforms:** `python3-cryptography` → `cryptography` (correct PyPI name)
**Params:** `{old_entry: "python3-cryptography", new_entry: "cryptography"}`

### PRIM-007: `service_name_remap`
**Applies to:** `.sh` files
**Detects:** Hardcoded Debian service names: `apache2`, `networking`, `mysql`
**Transforms:** Injects distro-dispatch service name block
**Params:** `{debian_name: "apache2"}`

**Catalog boundary:** These 7 primitives cover the demo app completely. Applications
requiring transformations outside this catalog are reported UNSUPPORTED for the
failing targets, with the exact error included.

---

## 5. AI Workflow

### Mode A — Adaptation Planning

Runs once per `adapt` invocation, after static analysis.

**Input to AI:**
```json
{
  "task": "plan_adaptations",
  "issues": [
    {"id": 0, "category": "package-manager", "severity": "error",
     "source_file": "setup.sh", "line": 3, "affected_targets": ["fedora","arch"],
     "explanation": "apt-get not available on Fedora or Arch"},
    ...
  ],
  "available_primitives": ["pkg_manager_call", "distro_path_check",
                            "env_var_portability", "shell_compat",
                            "package_name_remap", "requirements_normalize",
                            "service_name_remap"],
  "targets": ["ubuntu", "fedora", "arch"]
}
```

**Required AI output (JSON):**
```json
{
  "primitives": [
    {
      "primitive_id": "shell_compat",
      "params": {"strategy": "explicit_bash", "file": "setup.sh"},
      "issue_ids": [2],
      "rationale": "Normalize shebang before modifying shell script content"
    },
    {
      "primitive_id": "pkg_manager_call",
      "params": {"generic_name": "ssl-dev"},
      "issue_ids": [0],
      "rationale": "apt-get must be replaced with distro-dispatch block"
    }
  ]
}
```

**Validation:** Each `primitive_id` must exist in the catalog. Invalid IDs are dropped
and a warning is printed. If the entire response is invalid JSON, fall back to
`DeterministicProvider`.

**Deterministic fallback:** Maps `category` → default primitive:
```
"package-manager"  → pkg_manager_call
"library-name"     → package_name_remap
"hardcoded-path"   → distro_path_check
"env-assumption"   → env_var_portability
"shell-ism"        → shell_compat
"service-name"     → service_name_remap
```

### Mode B — Failure Diagnosis

Runs when any `StageResult.exit_code != 0`.

**Input to AI:**
```json
{
  "task": "diagnose_failure",
  "distro": "fedora",
  "stage": "prepare",
  "exit_code": 1,
  "stdout": "...",
  "stderr": "bash: apt-get: command not found\n",
  "applied_diffs_so_far": [
    {"primitive_id": "shell_compat", "target_file": "setup.sh", ...}
  ],
  "available_primitives": ["pkg_manager_call", ...]
}
```

**Required AI output:**
```json
{"primitive_id": "pkg_manager_call", "params": {"generic_name": "ssl-dev"}, "rationale": "..."}
```
OR:
```json
{"action": "give_up", "reason": "Error is outside primitive catalog: custom C library not in normalization table"}
```

**Deterministic fallback:** Parse `stderr` for known patterns:
```
"command not found: apt-get"  → pkg_manager_call
"No package ... found"        → package_name_remap
"/etc/debian_version: No such file"  → distro_path_check
"KeyError: DEBIAN_FRONTEND"   → env_var_portability
```

### Loop Logic

```
iteration = 0
working_tree = copy of source dir
best_diffs = []

while iteration < MAX_ITERATIONS:
    plan = ai.plan_adaptations(issues, catalog)      # Mode A (iteration 0 only)
    apply plan to working_tree, record diffs
    matrix = build_matrix(working_tree)

    if matrix.all_passed():
        best_diffs = current diffs
        break

    for failed_result in matrix.failed_results():
        repair = ai.diagnose_failure(failed_result, current_diffs, catalog)
        if repair.action == "give_up":
            mark target UNSUPPORTED
            continue
        reset working_tree to best_diffs state
        apply repair primitive
        record new diff
    
    iteration += 1

if best_diffs or any target passed:
    package(working_tree, manifest)
else:
    report all UNSUPPORTED with reasons
```

---

## 6. Docker Test Matrix

For each (distro, stage), the engine runs a single Docker container invocation:

```
docker run --rm -v <working_tree>:/src <image> bash /src/bf_scripts/<distro>/<stage>.sh
```

### Stage Scripts (generated by `build/script_gen.py` from manifest)

**prepare.sh** (Ubuntu example):
```bash
#!/bin/bash
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y libssl-dev python3 python3-pip
pip3 install -r /src/requirements.txt
```

**build.sh** (all distros — Python app has no separate build step):
```bash
#!/bin/bash
set -euo pipefail
cd /src
python3 -m py_compile app.py
echo "BUILD OK"
```

**test.sh** (all distros):
```bash
#!/bin/bash
set -euo pipefail
cd /src
python3 app.py
```

### Matrix Output Format

```
         PREPARE   BUILD    TEST    RESULT
Ubuntu    ✅        ✅       ✅      COMPATIBLE  (2.3s / 0.8s / 0.4s)
Fedora    ✅        ✅       ✅      COMPATIBLE  (18.4s / 0.7s / 0.4s)
Arch      ✅        ✅       ✅      COMPATIBLE  (31.2s / 0.7s / 0.4s)
```

A target is COMPATIBLE only when all three stages exit 0.
A target is FAILED when any stage exits non-zero (remaining stages are skipped).
A target is UNSUPPORTED when AI declares `give_up` for all repair attempts.

### Docker Availability

If `docker info` fails:
- `adapt` runs in `--dry-run` mode automatically: static analysis + AI plan + diffs
  are generated, but no build/test is run
- Manifest includes `"verified": false` and lists diffs as UNVERIFIED
- User is warned clearly

---

## 7. Package Format

### `.bfpack` Structure (tar.gz)

```
<name>-<version>.bfpack
├── manifest.json
├── source/                  — patched working tree (all primitives applied)
│   ├── app.py
│   ├── setup.sh             (patched)
│   └── requirements.txt     (patched)
├── diffs/
│   ├── 001-shell_compat.patch
│   ├── 002-pkg_manager_call.patch
│   ├── 003-distro_path_check.patch
│   └── 004-env_var_portability.patch
└── bf_scripts/
    ├── ubuntu/
    │   ├── prepare.sh
    │   ├── build.sh
    │   └── test.sh
    ├── fedora/
    │   └── ...
    └── arch/
        └── ...
```

### `manifest.json` Schema

```json
{
  "name": "portable-demo",
  "version": "0.1.0",
  "description": "...",
  "portability_score": 0.97,
  "portability_score_before": 0.32,
  "verified": true,
  "targets": {
    "ubuntu": {"status": "COMPATIBLE", "prepare": true, "build": true, "test": true},
    "fedora": {"status": "COMPATIBLE", "prepare": true, "build": true, "test": true},
    "arch":   {"status": "COMPATIBLE", "prepare": true, "build": true, "test": true}
  },
  "applied_adaptations": [
    {
      "sequence": 1,
      "primitive_id": "shell_compat",
      "params": {"strategy": "explicit_bash", "file": "setup.sh"},
      "reason": "Normalize shebang before modifying shell content",
      "affected_targets": ["fedora", "arch"],
      "diff_file": "diffs/001-shell_compat.patch",
      "verification_method": "docker-test",
      "iteration": 0
    }
  ],
  "issues": [...],
  "ai_summary": "Four portability issues were detected and resolved automatically...",
  "ai_provider": "watsonx" | "deterministic",
  "compatibility_cache_key": "sha256:abcdef...",
  "created_at": "2025-01-01T00:00:00Z"
}
```

### Installation Flow

```
black-flag install portable-demo-0.1.0.bfpack

1. Extract to ~/.local/share/bfpack/portable-demo/
2. Detect distro: runtime/detector.py reads /etc/os-release → "ubuntu"
3. Run bf_scripts/ubuntu/prepare.sh    (install system deps)
4. Run bf_scripts/ubuntu/build.sh      (build in place)
5. Run bf_scripts/ubuntu/test.sh       (smoke test)
6. On success: symlink source/app.py → ~/.local/bin/portable-demo
7. Print: "portable-demo installed successfully on ubuntu"
```

---

## 8. CLI Design

### Primary Commands

```
black-flag adapt <source-dir>
    --max-iterations N     (default: 3)
    --target t1,t2,t3      (default: ubuntu,fedora,arch)
    --dry-run              (analyze + plan + diffs; no Docker)
    --no-ai                (force deterministic provider)
    → Runs full loop; produces <name>-<version>.bfpack

black-flag analyze <source-dir>
    → Static analysis only; prints issue table and portability score
    → No patching, no Docker

black-flag install <file.bfpack>
    → Extracts, detects distro, prepares, builds, installs, symlinks

black-flag status [<name>]
    → Shows installed packages, applied diffs, per-distro results
```

### Secondary Commands (P1)

```
black-flag rollback <name> [--to-iteration N]
black-flag cache clear
black-flag dashboard [<manifest.json>]   (textual TUI)
```

### Live Output During `adapt`

```
🔍 Analyzing examples/portable-demo/ ...
  Found 4 portability issues (2 errors, 1 warning, 1 error)
  Portability score: 0.32 🔴

🤖 Planning adaptations (watsonx.ai) ...
  → PRIM-004 shell_compat       setup.sh:1  [shebang normalization]
  → PRIM-001 pkg_manager_call   setup.sh:3  [apt→distro dispatch]
  → PRIM-002 distro_path_check  app.py:6    [/etc/debian_version]
  → PRIM-003 env_var_portability app.py:12  [DEBIAN_FRONTEND]

🔧 Applying 4 adaptations ...
  ✅ 001-shell_compat.patch
  ✅ 002-pkg_manager_call.patch
  ✅ 003-distro_path_check.patch
  ✅ 004-env_var_portability.patch

🐳 Running build matrix (iteration 1/3) ...
             PREPARE   BUILD    TEST
  Ubuntu      ✅        ✅       ✅
  Fedora      ✅        ✅       ✅
  Arch        ✅        ✅       ✅

✅ All targets compatible. Portability score: 0.97 🟢
📦 Created: portable-demo-0.1.0.bfpack
```

---

## 9. Demo Application (`examples/portable-demo/`)

A small Python/shell tool that reports system information and SSL version.
Contains exactly 4 portability flaws — each mapped to one primitive.

### `setup.sh` (Flaws 1 + 2)

```bash
#!/bin/bash
# Flaw 1 (PRIM-004): /bin/bash shebang; [[ is bash-ism
# Flaw 2 (PRIM-001): apt-get is Debian/Ubuntu-specific
apt-get install -y libssl-dev python3-cryptography

if [[ -z "$VIRTUAL_ENV" ]]; then
    echo "Not in a virtualenv"
fi
```

### `app.py` (Flaws 3 + 4)

```python
#!/usr/bin/env python3
import os, ssl

# Flaw 3 (PRIM-002): /etc/debian_version does not exist on Fedora/Arch
if os.path.exists("/etc/debian_version"):
    with open("/etc/debian_version") as f:
        print(f"Debian version: {f.read().strip()}")
else:
    raise RuntimeError("This application requires a Debian-based system")

# Flaw 4 (PRIM-003): DEBIAN_FRONTEND is not set on Fedora/Arch — raises KeyError
FRONTEND = os.environ["DEBIAN_FRONTEND"]

ctx = ssl.create_default_context()
print(f"SSL {ssl.OPENSSL_VERSION} | frontend={FRONTEND}")
print("PASS: sysinfo check completed")
```

### `test.sh`

```bash
#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")"
python3 app.py
echo "TEST PASSED"
```

### Expected Before/After Matrix

**Before adaptation:**
```
         PREPARE   BUILD    TEST    RESULT
Ubuntu    ✅        ✅       ✅      COMPATIBLE
Fedora    ❌        —        —       PREPARE FAILED (apt-get not found)
Arch      ❌        —        —       PREPARE FAILED (apt-get not found)
```

**After adaptation (1 iteration):**
```
         PREPARE   BUILD    TEST    RESULT
Ubuntu    ✅        ✅       ✅      COMPATIBLE
Fedora    ✅        ✅       ✅      COMPATIBLE
Arch      ✅        ✅       ✅      COMPATIBLE
```

### Applied Diffs (human-readable, stored in `.bfpack`)

`diffs/002-pkg_manager_call.patch`:
```diff
--- a/setup.sh
+++ b/setup.sh
@@ -1,3 +1,9 @@
-apt-get install -y libssl-dev python3-cryptography
+_BF_DISTRO=$(. /etc/os-release && echo "$ID")
+case "$_BF_DISTRO" in
+  ubuntu|debian) apt-get install -y libssl-dev python3-cryptography ;;
+  fedora|rhel)   dnf install -y openssl-devel python3-cryptography ;;
+  arch)          pacman -S --noconfirm openssl python-cryptography ;;
+  *) echo "Unsupported distro: $_BF_DISTRO" && exit 1 ;;
+esac
```

---

## 10. Portability Score Formula

The score is fully deterministic and derived from the issue list.
AI output does NOT affect the score.

```
score = 1.0 - (weighted_penalty / max_possible_penalty)

weighted_penalty = Σ (severity_weight[severity] × target_weight[n_affected_targets])

severity_weight:   error=1.0 | warning=0.4 | info=0.1
target_weight:     1 target=0.5 | 2 targets=0.75 | 3 targets=1.0

max_possible_penalty = max(weighted_penalty, 5.0)
score = clamp(score, 0.0, 1.0)
```

**Demo app (before):** 3 errors × 2 targets + 1 error × 2 targets = 4 × 0.75 = 3.0 penalty → score = 1 - 3.0/5.0 = **0.40** 🔴

**Demo app (after):** 0 issues → score = **1.0** ✅ (reported as 0.97 to reflect 1 info-level residual warning)

**Bands:**
| Score | Indicator | Meaning |
|---|---|---|
| 0.00–0.40 | 🔴 Not portable | Will fail on non-native distros |
| 0.40–0.70 | 🟡 Partially portable | Needs manual adaptation |
| 0.70–0.90 | 🟢 Mostly portable | Minor adjustments needed |
| 0.90–1.00 | ✅ Portable | No action needed |

---

## 11. P0 / P1 / P2 Scope

### P0 — The Working Adaptation Loop (~38h for 2 people)

ALL of these must work or the demo fails.

| Feature | Hours | Notes |
|---|---|---|
| Project scaffold, pyproject.toml, entry point | 1h | |
| `core/types.py` — all 8 dataclasses | 1h | |
| 3 DistroAdapter implementations + normalization table | 2.5h | |
| `runtime/detector.py` | 0.5h | |
| Python static analyzer (ast-based) | 3h | |
| Shell static analyzer (regex) | 1h | |
| C static analyzer (regex) | 1h | |
| `analyzer/runner.py` | 0.5h | |
| Primitive catalog: all 7 primitives | 5h | pattern matching + patch output per primitive |
| `adaptation_engine/patcher.py` + `rollback.py` | 2.5h | |
| AI deterministic provider (rules + stderr patterns) | 2h | |
| AI watsonx provider (httpx + JSON parsing + validation) | 2h | |
| AI factory + prompts | 0.5h | |
| `core/cache.py` | 1h | |
| `core/manifest.py` + score formula | 1h | |
| `build/container.py` + Docker availability check | 1h | |
| `build/script_gen.py` (3 stages × 3 distros) | 2h | |
| `build/matrix.py` (orchestrate + return results) | 2h | |
| Repair loop in `adapt` command (max 3 iterations) | 2.5h | rollback + re-apply + rerun |
| `packager/packer.py` | 1.5h | |
| `installer/installer.py` | 1.5h | |
| `cli/main.py` — 4 commands wired with rich output | 2.5h | |
| Demo app with 4 deliberate flaws | 1h | |
| `scripts/pull-images.sh` | 0.5h | |
| End-to-end smoke test | 1h | |
| **Total** | **~40h** | |

### P1 — Improved AI and UX (~5h, if time allows)

| Feature | Hours |
|---|---|
| `textual` TUI dashboard (score gauge, matrix, diff viewer) | 3h |
| Before/after score comparison in adapt output | 0.5h |
| `rollback` + `cache clear` CLI commands | 1h |
| Performance benchmarks (startup time, memory) | 0.5h |

### P2 — Post-hackathon

Explicitly cut from 48h window:
- C/C++ source code modification
- Makefile / CMake adaptation
- `.bfpack` signature verification
- ARM / RISC-V / Windows / macOS
- Primitive catalog expansion beyond 7
- Custom primitive authoring DSL
- CI integration

---

## 12. 48-Hour Timeline

### Phase 1 (0–8h): Scaffold + Core Types + Adapters + CLI Skeleton

**Goal:** Project installs; CLI entry point exists; all adapters and types are defined.
Parallelizable: Developer A builds scaffold + types; Developer B builds demo app + pulls Docker images.

- [ ] `pyproject.toml` with deps: typer, rich, httpx; entry point `black-flag`
- [ ] All `__init__.py` files; package structure
- [ ] `core/types.py` — all dataclasses and enums (PortabilityIssue, CompatibilityManifest, AdaptationPlan, AppliedDiff, StageResult, BuildMatrix, RepairAction)
- [ ] `adapters/normalization.py` — 30-package generic→distro table
- [ ] `adapters/base.py` + `ubuntu.py` + `fedora.py` + `arch.py`
- [ ] `runtime/detector.py`
- [ ] `cli/main.py` — all 4 commands stubbed with `typer.echo("TODO")`
- [ ] `examples/portable-demo/` with 4 deliberate flaws
- [ ] `scripts/pull-images.sh`; pull all 3 Docker images (run in background)
- **Gate:** `black-flag --help` works; `AptAdapter().install_dependency("ssl-dev")` returns correct string

---

### Phase 2 (8–16h): Static Analyzer + Primitive Catalog

**Goal:** `analyze` works end-to-end; all 7 primitives can transform the demo app files correctly.
Critical path: primitive catalog correctness is the foundation of the entire loop.

- [ ] `analyzer/python_analyzer.py` — ast.walk for subprocess calls with pkg manager strings, hardcoded `/etc/` paths, Debian-specific env var subscripts
- [ ] `analyzer/shell_analyzer.py` — regex for apt/dnf/pacman calls; `[[`; `#!/bin/bash`
- [ ] `analyzer/c_analyzer.py` — regex for system/popen, Debian paths
- [ ] `analyzer/runner.py` — dispatch by file extension, aggregate issues
- [ ] `primitives/base.py` — `AdaptationPrimitive` ABC with `matches()` and `apply()`
- [ ] `primitives/catalog.py` — dict of primitive_id → primitive instance
- [ ] PRIM-001 `pkg_manager.py` — detect apt-get line; produce distro-dispatch block using normalization table
- [ ] PRIM-002 `path_normalize.py` — detect `/etc/debian_version`; inject `_bf_detect_distro()` + replace check
- [ ] PRIM-003 `env_portability.py` — detect `os.environ["DEBIAN_FRONTEND"]`; replace with `.get()`
- [ ] PRIM-004 `shell_compat.py` — detect `#!/bin/bash` + `[[`; apply `explicit_bash` strategy
- [ ] PRIM-005, 006, 007 — name remap in shell/requirements/services
- [ ] Wire `analyze` command: run analyzer, print rich issue table with score
- [ ] Unit tests: each primitive `apply()` produces correct output on known input
- **Gate:** `black-flag analyze examples/portable-demo/` detects all 4 issues; manually calling `PRIM-001.apply()` on `setup.sh` produces the correct distro-dispatch block

---

### Phase 3 (16–24h): Adaptation Engine + AI Integration

**Goal:** `adapt --dry-run` applies all 4 primitives, produces diffs, and shows AI plan.
This phase is where the product becomes an engine rather than a scanner.

- [ ] `adaptation_engine/diff_record.py` — `AppliedDiff` dataclass
- [ ] `adaptation_engine/patcher.py` — call `primitive.apply()`; compute before/after diff; write `.patch` file; record `AppliedDiff`
- [ ] `adaptation_engine/rollback.py` — reset working tree to a prior diff list state
- [ ] `core/cache.py` — sha256 keying; JSON store at `~/.cache/blackflag/`
- [ ] `ai/provider.py` ABC
- [ ] `ai/prompts.py` — Mode A and Mode B prompt templates with JSON schema instructions
- [ ] `ai/deterministic.py` — category→primitive mapping; stderr→repair primitive mapping
- [ ] `ai/watsonx.py` — POST to `/v1/chat/completions`; parse JSON response; validate primitive IDs against catalog; fallback on failure
- [ ] `ai/factory.py` — env var check, return provider
- [ ] `adaptation_engine/planner.py` — call AI provider; return validated `AdaptationPlan`
- [ ] Wire `adapt --dry-run` command: analyze → plan (AI) → patch all primitives → print diff list
- **Gate:** `black-flag adapt examples/portable-demo/ --dry-run` applies 4 patches and writes 4 `.patch` files; AI plan is printed with primitive IDs and rationale; `--no-ai` flag uses deterministic provider

---

### Phase 4 (24–32h): Docker Build/Test Matrix

**Goal:** `adapt` (without `--dry-run`) runs the matrix and shows per-distro PREPARE/BUILD/TEST results.
Expected: Ubuntu ✅ before adaptation; Fedora/Arch ❌ on un-patched source.

- [ ] `build/container.py` — `docker run --rm -v` wrapper; check Docker availability; capture all output; timeout handling
- [ ] `build/script_gen.py` — write `prepare.sh`, `build.sh`, `test.sh` for each distro from manifest; include Arch keyring step in prepare.sh
- [ ] `build/result.py` — `StageResult`, `BuildMatrix`
- [ ] `build/matrix.py` — run 3 stages × 3 distros sequentially; return `BuildMatrix`; print live progress table
- [ ] Wire matrix into `adapt` command loop after patching step
- [ ] Verify: un-patched demo app → Fedora PREPARE FAILED (apt-get not found); patched → all pass
- **Gate:** `black-flag adapt examples/portable-demo/` (single iteration, no repair) runs matrix; Ubuntu passes before and after; Fedora/Arch fail before adaptation and pass after

---

### Phase 5 (32–40h): Repair Loop + Packager + Installer

**Goal:** Full end-to-end loop closes; `.bfpack` is produced; `install` works.

- [ ] Add repair loop to `adapt` command: on any failure, call `ai.diagnose_failure()`; apply repair primitive; regenerate scripts; rerun matrix; max 3 iterations
- [ ] Rollback on failure before repair: wipe working tree, re-apply best diffs so far
- [ ] Termination: all pass → package; max iterations → report best; `give_up` → mark UNSUPPORTED
- [ ] `packager/packer.py` — tar.gz patched source + diffs + bf_scripts + manifest.json
- [ ] `installer/installer.py` — extract to `~/.local/share/bfpack/<name>/`; detect distro; run prepare+build; symlink to `~/.local/bin/`
- [ ] Wire `pack` (for manual packaging) and `install` commands
- [ ] `status` command — list installed packages, show manifest summary
- [ ] End-to-end smoke test: `adapt` → `.bfpack` → `install` → run installed app
- **Gate:** `black-flag adapt examples/portable-demo/` produces `portable-demo-0.1.0.bfpack` with all targets COMPATIBLE; `black-flag install portable-demo-0.1.0.bfpack` succeeds on host distro; running the installed app prints "PASS"

---

### Phase 6 (40–48h): Polish + Dashboard + Docs + Demo Rehearsal

**Goal:** Demo-ready: polished output, docs complete, demo rehearsed, no terminal intervention needed.

- [ ] Polish `adapt` live output: progress bars, iteration counter, before/after score comparison
- [ ] `dashboard/app.py` — rich layout: issue table, before/after score, matrix table, diff list (textual TUI if time allows)
- [ ] Wire `dashboard` command
- [ ] `README.md` — install instructions, quickstart, score explanation, known limitations
- [ ] `docs/architecture.md` — data flow, component roles
- [ ] `examples/portable-demo/README.md` — explains each flaw and which primitive fixes it
- [ ] Final smoke test on a clean environment (Docker container or VM)
- [ ] Demo rehearsal: `analyze → adapt → show diffs → install → status` in 5 minutes
- **Gate:** Full demo runs end-to-end in under 5 minutes with zero manual intervention

---

## 13. Technical Risks (Final Assessment)

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| AI response not valid JSON (hallucinated primitives) | High | High | Strict JSON schema in prompt; validate all primitive IDs against catalog; fallback to deterministic |
| Arch keyring update times out (2–3 min overhead) | Medium | Medium | Pre-pull images before demo; bake keyring step into prepare.sh; set 5-min timeout per stage |
| Primitive pattern not found in file (PrimitiveNotApplicable) | Medium | Medium | Primitives raise rather than silently partial-apply; issue flagged UNSUPPORTED with explanation |
| Repair loop makes things worse across iterations | Medium | Medium | Track "best matrix" across iterations; roll back to best before applying next repair |
| Demo takes > 5 min due to Docker overhead | High | High | Pre-pull images; parallelize Ubuntu+Fedora runs; have pre-run result screenshots as backup |
| `patcher.py` produces invalid Python after PRIM-002/003 | Low | High | Unit test every primitive on the demo app before Phase 4 begins |
| watsonx.ai endpoint unavailable during demo | Medium | Low | `--no-ai` flag always available; deterministic provider is indistinguishable in output format |
| Phase 5 slips (repair loop complexity) | Medium | High | Repair loop can be stubbed: run matrix, if fail → print UNSUPPORTED (no AI repair). Package whatever passed. This keeps the demo working with a weaker claim. |

---

## 14. Known Limitations

```markdown
## Known Limitations

Black Flag is a 48-hour hackathon prototype. The following limitations are by design.

### Portability Envelope
Black Flag supports a bounded set of 7 adaptation primitives. It does not claim to
make arbitrary applications portable. Applications requiring transformations outside
this set are reported as UNSUPPORTED for the affected targets, with the exact
build/test failure output included.

### Adaptation Scope
Automatic modification is limited to: shell scripts (.sh), Python source files (.py),
and requirements.txt. C/C++ source files, Makefiles, CMake configurations, and binary
artifacts are not modified.

### Static Analysis Depth
Python analysis uses the AST module. C analysis is regex-based. Neither performs
type inference, import tracing, macro expansion, or cross-file data-flow analysis.
Issues inside complex conditionals or runtime-generated code may not be detected.

### Build Model
.bfpack does not contain pre-compiled binaries. The application is compiled/built
from patched source at install time. Build toolchains (pip, gcc, etc.) must be
available on the target machine.

### Package Name Mapping
The normalization table covers ~30 commonly used packages. Packages outside this
table generate a WARNING and are not automatically mapped.

### Docker Requirement
The adaptation loop requires Docker for build/test verification. Without Docker,
Black Flag runs in --dry-run mode: analysis and diffs are produced but not verified.

### Repair Loop
The repair loop runs a maximum of 3 iterations. Applications requiring more than
3 repair cycles are partially adapted and the remaining failures reported as
UNSUPPORTED.

### AI
AI errors are bounded by the primitive catalog — it cannot generate arbitrary code.
If the AI returns an invalid response, the deterministic provider is used transparently.
The AI does not run during installation or application execution.

### Security
.bfpack files execute install and build scripts with the user's permissions.
Do not install untrusted .bfpack files. No signature verification is implemented.
```

---

## 15. Definition of Done

The hackathon demo is complete when ALL of the following are true:

1. `black-flag analyze examples/portable-demo/` detects ≥4 portability issues with correct category, severity, and affected targets
2. `black-flag adapt examples/portable-demo/` runs the full ANALYZE→PLAN→ADAPT→BUILD→TEST loop end-to-end
3. After adaptation, all 3 distros show PREPARE ✅ BUILD ✅ TEST ✅ in the matrix
4. Applied diffs are written to the output directory and are human-readable unified diff format
5. A `.bfpack` file is produced containing patched source, diffs, bf_scripts, and manifest.json
6. `black-flag install <name>.bfpack` installs and runs correctly on the host distro
7. Before portability score ≤ 0.40 (🔴); after portability score ≥ 0.85 (🟢)
8. The AI planning step is visible in output — either watsonx.ai response or deterministic with source label
9. `--dry-run` works without Docker (produces diffs, no containers run)
10. `--no-ai` works (deterministic provider used, output format identical)
11. Full demo runs in under 5 minutes without manual terminal intervention
12. README accurately describes install, quickstart, and known limitations

---

## Implementation Sub-Tasks (for Agent Mode)

Each sub-task maps to one Phase. Process one at a time; read this plan file for context before starting each sub-task.

---

### Sub-Task 1: Scaffold + Core Types + Adapters + CLI Skeleton
**Status:** [ ] pending
**Corresponds to:** Phase 1 (0–8h)
**Intent:** Establish project structure and all shared types. Every subsequent sub-task depends on these.
**Expected Outcomes:** `pip install -e .` succeeds; `black-flag --help` works; all adapters instantiate; `PortabilityIssue` and all other dataclasses exist and are importable.
**Todo:**
- [ ] `pyproject.toml` with entry point and deps
- [ ] All `__init__.py` files and package structure
- [ ] `core/types.py` — all 8 dataclasses
- [ ] `adapters/normalization.py` — 30-package table
- [ ] `adapters/base.py` + `ubuntu.py` + `fedora.py` + `arch.py`
- [ ] `runtime/detector.py`
- [ ] `cli/main.py` — all commands stubbed
- [ ] `examples/portable-demo/` with 4 deliberate flaws
- [ ] `scripts/pull-images.sh`

---

### Sub-Task 2: Static Analyzer + Primitive Catalog
**Status:** [ ] pending
**Corresponds to:** Phase 2 (8–16h)
**Intent:** Implement the analyzer and all 7 primitives. Primitives must correctly transform the demo app files.
**Expected Outcomes:** `black-flag analyze examples/portable-demo/` detects all 4 issues; every primitive `apply()` produces correct output when tested on known inputs.
**Todo:**
- [ ] `analyzer/python_analyzer.py`, `shell_analyzer.py`, `c_analyzer.py`, `runner.py`
- [ ] `primitives/base.py`, `catalog.py`
- [ ] All 7 primitive implementations with `matches()` and `apply()`
- [ ] Wire `analyze` CLI command with rich output
- [ ] Unit tests for each primitive

---

### Sub-Task 3: Adaptation Engine + AI Integration
**Status:** [ ] pending
**Corresponds to:** Phase 3 (16–24h)
**Intent:** AI selects primitives; patcher applies them to a working tree; diffs are written.
**Expected Outcomes:** `black-flag adapt examples/portable-demo/ --dry-run` applies 4 patches, writes 4 `.patch` files, and displays the AI plan with primitive IDs and rationale.
**Todo:**
- [ ] `adaptation_engine/diff_record.py`, `patcher.py`, `rollback.py`
- [ ] `core/cache.py`
- [ ] `ai/provider.py`, `prompts.py`, `deterministic.py`, `watsonx.py`, `factory.py`
- [ ] `adaptation_engine/planner.py`
- [ ] Wire `adapt --dry-run` command

---

### Sub-Task 4: Docker Build/Test Matrix
**Status:** [ ] pending
**Corresponds to:** Phase 4 (24–32h)
**Intent:** Real build+test results per distro, not just install script exit codes.
**Expected Outcomes:** `black-flag adapt examples/portable-demo/` runs matrix; Ubuntu passes before and after; Fedora/Arch fail on un-patched source and pass after adaptation.
**Todo:**
- [ ] `build/container.py` with Docker availability check
- [ ] `build/script_gen.py` — 3 stages × 3 distros
- [ ] `build/result.py`, `build/matrix.py`
- [ ] Wire matrix into adapt loop; live progress table
- [ ] Arch keyring handling in prepare.sh

---

### Sub-Task 5: Repair Loop + Packager + Installer
**Status:** [ ] pending
**Corresponds to:** Phase 5 (32–40h)
**Intent:** Close the loop — diagnose, repair, retest, package, install.
**Expected Outcomes:** Full pipeline: `adapt` → `.bfpack` → `install` → run. All targets COMPATIBLE in manifest. `install` succeeds on host distro.
**Todo:**
- [ ] Repair loop in `adapt` (max 3 iterations, rollback on failure)
- [ ] `packager/packer.py`
- [ ] `installer/installer.py`
- [ ] Wire `install` and `status` commands
- [ ] End-to-end smoke test

---

### Sub-Task 6: Polish + Dashboard + Docs + Demo Rehearsal
**Status:** [ ] pending
**Corresponds to:** Phase 6 (40–48h)
**Intent:** Demo-ready state: polished output, documentation, rehearsed 5-minute demo.
**Expected Outcomes:** Full demo runs in under 5 minutes; README is accurate; before/after score comparison visible; diffs are inspectable.
**Todo:**
- [ ] `dashboard/app.py` — rich layout or textual TUI
- [ ] Polish `adapt` live output
- [ ] `README.md`, `docs/architecture.md`, `examples/portable-demo/README.md`
- [ ] Final smoke test on clean environment
- [ ] Demo rehearsal
