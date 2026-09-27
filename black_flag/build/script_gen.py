"""
build/script_gen.py — Generate per-distro PREPARE / BUILD / TEST stage scripts.

Generated layout inside the working tree:

    <working_tree>/bf_scripts/
        ubuntu/
            prepare.sh
            build.sh
            test.sh
        fedora/
            prepare.sh
            build.sh
            test.sh
        arch/
            prepare.sh
            build.sh
            test.sh

CRITICAL ARCHITECTURAL CONSTRAINT — the PREPARE stage MUST actually invoke
the *adapted application's own setup.sh* (the one Black Flag has patched via
primitives).  It does NOT replace the application's setup.  The sequence for
each distro is:

    PREPARE   — install base OS packages (python3, pip, bash, ...) using the
                distro's native package manager, then run the APPLICATION'S
                patched setup.sh.  For the portable-demo this means that the
                `apt-get install libssl-dev …` line in setup.sh has already
                been rewritten to a _BF_DISTRO dispatch block by PRIM-001,
                so running setup.sh directly will install the correct
                distro-specific SSL packages on Ubuntu / Fedora / Arch.

    BUILD     — pip install -r requirements.txt into the container (optional)

    TEST      — run the APPLICATION'S own test.sh verbatim.

Keeping the application's scripts as the authority means the demo honestly
proves "original code fails → Black Flag patches it → patched code works".
"""
from __future__ import annotations

from pathlib import Path

from black_flag.adapters.normalization import resolve_package
from black_flag.runtime.detector import adapter_for_distro, get_all_adapters


# ---------------------------------------------------------------------------
# Shared shell helpers written into every prepare.sh
# ---------------------------------------------------------------------------

_PREPARE_PREAMBLE = """\
#!/usr/bin/env bash
#
# Black Flag generated stage script: PREPARE — {distro}
#
# DO NOT EDIT — regenerated every matrix iteration by build/script_gen.py
#
set -euo pipefail
cd "$(dirname "$0")/../.."  # back to <working_tree>/

echo "[black-flag] PREPARE stage for {distro} ($(date -Iseconds))"
"""

_BUILD_PREAMBLE = """\
#!/usr/bin/env bash
#
# Black Flag generated stage script: BUILD — {distro}
#
set -euo pipefail
cd "$(dirname "$0")/../.."
echo "[black-flag] BUILD stage for {distro} ($(date -Iseconds))"
"""

_TEST_PREAMBLE = """\
#!/usr/bin/env bash
#
# Black Flag generated stage script: TEST — {distro}
#
set -euo pipefail
cd "$(dirname "$0")/../.."
echo "[black-flag] TEST stage for {distro} ($(date -Iseconds))"
"""

# ---------------------------------------------------------------------------
# Per-distro prepare.sh: install base toolchain, invoke the APPLICATION'S setup.sh
# ---------------------------------------------------------------------------

# Distro-specific one-liner that installs:
#   python3, python3-pip, bash, ca-certificates
# plus the SSL development headers for the ssl module.
#
# NOTE: names below are written as distro-specific names directly — these
# are for the *Black Flag base toolchain*, not the user's application, so
# they don't go through the normalization table.

_UBUNTU_PREPARE_BASE = """\
export DEBIAN_FRONTEND=noninteractive
apt-get update -y >/dev/null
apt-get install -y --no-install-recommends \\
    ca-certificates bash python3 python3-pip python3-venv \\
    >/dev/null
"""

_FEDORA_PREPARE_BASE = """\
# Fedora repos sometimes need metadata refresh before install
dnf makecache --refresh -y 2>/dev/null || true
dnf install -y --setopt=install_weak_deps=False \\
    ca-certificates bash python3 python3-pip \\
    >/dev/null
"""

# Arch requires a keyring refresh before installing *any* package because
# the rolling-release image ships with stale signing keys.
# See AGENTS.md: "Docker images" section for Arch requirement.
_ARCH_PREPARE_BASE = """\
pacman -Sy --noconfirm archlinux-keyring >/dev/null
pacman -S --noconfirm \\
    ca-certificates bash python python-pip \\
    >/dev/null
"""

# After base toolchain we run the APPLICATION'S OWN ADAPTED setup.sh.
# We chmod +x defensively because Windows working-trees don't always
# preserve POSIX executable bits across the copy + Docker volume mount.
_RUN_APP_SETUP = """\
# -----------------------------------------------------------------------------
# Step 2 — execute the APPLICATION'S own adapted setup.sh
#
# This setup.sh was patched by Black Flag's primitives (e.g. PRIM-001 replaced
# `apt-get install libssl-dev` with a _BF_DISTRO dispatch block).  Running
# the *patched* setup.sh directly is how the engine proves it actually
# made the application portable — it is NOT a synthetic Black Flag script.
# -----------------------------------------------------------------------------
if [ -f ./setup.sh ]; then
    chmod +x ./setup.sh
    echo "[black-flag]   → executing adapted setup.sh"
    bash ./setup.sh
    echo "[black-flag]   → adapted setup.sh completed"
else
    echo "[black-flag]   → no setup.sh in working tree, skipping"
fi
"""

# ---------------------------------------------------------------------------
# Per-distro build.sh: pip install requirements.txt
# ---------------------------------------------------------------------------

_BUILD_PIP_STEP = """\
if [ -f ./requirements.txt ]; then
    echo "[black-flag]   → pip install -r requirements.txt"
    python3 -m pip install --quiet --disable-pip-version-check -r ./requirements.txt \\
        2>&1 | tail -n 5 || true
    echo "[black-flag]   → pip install done"
else
    echo "[black-flag]   → no requirements.txt, skipping pip install"
fi
"""

# ---------------------------------------------------------------------------
# Per-distro test.sh: run the APPLICATION'S test.sh
# ---------------------------------------------------------------------------

_TEST_STEP = """\
# Run the APPLICATION'S own smoke test — this is the real acceptance check.
if [ -f ./test.sh ]; then
    chmod +x ./test.sh
    echo "[black-flag]   → executing test.sh"
    bash ./test.sh
    echo "[black-flag]   → test.sh passed"
else
    # Fall back to app.py if the project has no test.sh
    if [ -f ./app.py ]; then
        chmod +x ./app.py
        echo "[black-flag]   → no test.sh, running app.py directly"
        python3 ./app.py
        echo "[black-flag]   → app.py run complete"
    else
        echo "[black-flag]   ⚠ no test.sh or app.py found in working tree"
        exit 1
    fi
fi
"""


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def generate_scripts(working_tree: Path) -> None:
    """
    Create or overwrite the ``bf_scripts/<distro>/{prepare,build,test}.sh``
    stage scripts for every supported target distro.

    Called at the start of every matrix iteration (see cli/main.py step 5)
    so that changes made to the working tree by PRIM-006 (requirements
    normalization) are reflected in the pip install command inside build.sh.
    """
    tree_root = working_tree.resolve()
    out_root = tree_root / "bf_scripts"
    out_root.mkdir(exist_ok=True)

    distro_bodies = {
        "ubuntu": _UBUNTU_PREPARE_BASE,
        "fedora": _FEDORA_PREPARE_BASE,
        "arch":   _ARCH_PREPARE_BASE,
    }

    for adapter in get_all_adapters():
        distro = adapter.distro_id
        distro_dir = out_root / distro
        distro_dir.mkdir(exist_ok=True)

        # ---------- prepare.sh ----------
        base = distro_bodies[distro]
        # Inject adapter env vars (e.g. DEBIAN_FRONTEND for ubuntu)
        env_lines = ""
        env = adapter.environment_configuration() or {}
        for k, v in env.items():
            env_lines += f"export {k}='{v}'\n"
        if env_lines:
            env_lines = "# Distro adapter environment\n" + env_lines + "\n"

        prepare_content = _PREPARE_PREAMBLE.format(distro=distro) + env_lines + (
            "\n# -----------------------------------------------------------------------------\n"
            "# Step 1 — install base OS toolchain packages (python3, pip, bash, …)\n"
            "# -----------------------------------------------------------------------------\n"
        ) + base + "\n" + _RUN_APP_SETUP

        _write_x(distro_dir / "prepare.sh", prepare_content)

        # ---------- build.sh ----------
        build_content = _BUILD_PREAMBLE.format(distro=distro) + env_lines + _BUILD_PIP_STEP
        _write_x(distro_dir / "build.sh", build_content)

        # ---------- test.sh ----------
        test_content = _TEST_PREAMBLE.format(distro=distro) + env_lines + _TEST_STEP
        _write_x(distro_dir / "test.sh", test_content)


def _write_x(path: Path, content: str) -> None:
    """
    Write *content* to *path* and make it executable (POSIX bit 0755).

    Line endings are forced to ``\\n`` (Unix LF) regardless of the host
    platform.  Windows-generated scripts would otherwise carry ``\\r\\n``
    which bash inside Linux containers cannot parse (every CR becomes a
    trailing ``\\r`` token causing ``command not found`` errors).
    """
    normalized = content.replace("\r\n", "\n").replace("\r", "\n")
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(normalized)
    try:
        path.chmod(0o755)
    except OSError:
        pass
