"""
tests/test_phase1.py — Phase 1 verification tests.

Covers:
  - core/types.py:  all dataclasses instantiate correctly
  - adapters:       AptAdapter, DnfAdapter, PacmanAdapter behave correctly
  - normalization:  resolve_package returns correct values
  - runtime/detector: adapter_for_distro works; detect_distro_id returns a string
  - CLI:            imports without error; basic attribute checks

Does NOT require Docker or a running Linux distro.
"""
from __future__ import annotations

import sys
import os


# ---------------------------------------------------------------------------
# core/types.py
# ---------------------------------------------------------------------------


def test_portability_issue_instantiation() -> None:
    from black_flag.core.types import PortabilityIssue

    issue = PortabilityIssue(
        category="package-manager",
        severity="error",
        source_file="setup.sh",
        line=3,
        affected_targets=["fedora", "arch"],
        explanation="apt-get is not available on Fedora or Arch",
        suggested_primitive="pkg_manager_call",
        verification_method="docker-test",
    )
    assert issue.category == "package-manager"
    assert issue.severity == "error"
    assert issue.line == 3
    assert "fedora" in issue.affected_targets
    assert issue.suggested_primitive == "pkg_manager_call"


def test_adaptation_plan_instantiation() -> None:
    from black_flag.core.types import AdaptationPlan, PrimitiveApplication

    plan = AdaptationPlan(
        applications=[
            PrimitiveApplication(
                primitive_id="pkg_manager_call",
                params={"generic_name": "ssl-dev"},
                issue_ids=[0],
                rationale="Replace apt-get with distro-dispatch block",
            )
        ],
        source="deterministic",
    )
    assert len(plan.applications) == 1
    assert plan.applications[0].primitive_id == "pkg_manager_call"
    assert plan.source == "deterministic"


def test_applied_diff_instantiation() -> None:
    from black_flag.core.types import AppliedDiff

    diff = AppliedDiff(
        sequence=1,
        primitive_id="pkg_manager_call",
        params={"generic_name": "ssl-dev"},
        target_file="setup.sh",
        diff_text="--- a/setup.sh\n+++ b/setup.sh\n@@ -1 +1 @@\n-apt-get install\n+case ...",
        reason="apt-get is Debian-specific",
        affected_targets=["fedora", "arch"],
        verification_method="docker-test",
        iteration=0,
    )
    assert diff.sequence == 1
    assert diff.primitive_id == "pkg_manager_call"


def test_stage_result_and_build_matrix() -> None:
    from black_flag.core.types import StageResult, BuildMatrix

    results = [
        StageResult("ubuntu", "prepare", 0, "ok", "", 1.2),
        StageResult("ubuntu", "build", 0, "ok", "", 0.5),
        StageResult("ubuntu", "test", 0, "PASS", "", 0.3),
        StageResult("fedora", "prepare", 1, "", "apt-get: command not found", 0.8),
    ]
    matrix = BuildMatrix(results=results, iteration=0)

    assert matrix.passed("ubuntu") is True
    assert matrix.passed("fedora") is False
    assert matrix.all_passed() is False
    assert len(matrix.failed_results()) == 1
    assert matrix.failed_results()[0].distro == "fedora"
    assert matrix.first_failure("fedora") is not None
    assert matrix.first_failure("ubuntu") is None


def test_repair_action_apply() -> None:
    from black_flag.core.types import RepairAction

    action = RepairAction(
        action="apply",
        primitive_id="pkg_manager_call",
        params={"generic_name": "ssl-dev"},
        rationale="apt-get not found in stderr",
    )
    assert action.action == "apply"
    assert action.primitive_id == "pkg_manager_call"


def test_repair_action_give_up() -> None:
    from black_flag.core.types import RepairAction

    action = RepairAction(
        action="give_up",
        primitive_id=None,
        params={},
        rationale="Error outside primitive catalog",
    )
    assert action.action == "give_up"
    assert action.primitive_id is None


def test_compatibility_manifest_instantiation() -> None:
    from black_flag.core.types import CompatibilityManifest, TargetResult

    manifest = CompatibilityManifest(
        name="portable-demo",
        version="0.1.0",
        description="Demo app",
        portability_score=0.97,
        portability_score_before=0.32,
        verified=True,
        targets={
            "ubuntu": TargetResult(status="COMPATIBLE", prepare=True, build=True, test=True),
            "fedora": TargetResult(status="COMPATIBLE", prepare=True, build=True, test=True),
            "arch":   TargetResult(status="COMPATIBLE", prepare=True, build=True, test=True),
        },
        applied_adaptations=[],
        issues=[],
        ai_summary="",
        ai_provider="deterministic",
        compatibility_cache_key="sha256:abc123",
        created_at="2025-01-01T00:00:00Z",
    )
    assert manifest.name == "portable-demo"
    assert manifest.portability_score == 0.97
    assert manifest.targets["ubuntu"].status == "COMPATIBLE"


# ---------------------------------------------------------------------------
# adapters/normalization.py
# ---------------------------------------------------------------------------


def test_resolve_package_ubuntu() -> None:
    from black_flag.adapters.normalization import resolve_package

    assert resolve_package("ssl-dev", "ubuntu") == "libssl-dev"


def test_resolve_package_fedora() -> None:
    from black_flag.adapters.normalization import resolve_package

    assert resolve_package("ssl-dev", "fedora") == "openssl-devel"


def test_resolve_package_arch() -> None:
    from black_flag.adapters.normalization import resolve_package

    assert resolve_package("ssl-dev", "arch") == "openssl"


def test_resolve_package_unknown_generic() -> None:
    from black_flag.adapters.normalization import resolve_package

    result = resolve_package("nonexistent-package-xyz", "ubuntu")
    assert result is None


def test_resolve_package_python_cryptography_arch() -> None:
    from black_flag.adapters.normalization import resolve_package

    # On Arch, the pip package is 'python-cryptography', not 'python3-cryptography'
    result = resolve_package("python-cryptography", "arch")
    assert result == "python-cryptography"


def test_resolve_service_ubuntu() -> None:
    from black_flag.adapters.normalization import resolve_service

    assert resolve_service("apache2", "ubuntu") == "apache2"


def test_resolve_service_fedora() -> None:
    from black_flag.adapters.normalization import resolve_service

    assert resolve_service("apache2", "fedora") == "httpd"


def test_resolve_service_arch() -> None:
    from black_flag.adapters.normalization import resolve_service

    assert resolve_service("apache2", "arch") == "httpd"


# ---------------------------------------------------------------------------
# adapters: AptAdapter
# ---------------------------------------------------------------------------


def test_apt_adapter_package_manager() -> None:
    from black_flag.adapters.ubuntu import AptAdapter

    adapter = AptAdapter()
    assert adapter.package_manager() == "apt-get"


def test_apt_adapter_install_dependency() -> None:
    from black_flag.adapters.ubuntu import AptAdapter

    adapter = AptAdapter()
    cmd = adapter.install_dependency("libssl-dev")
    assert "apt-get" in cmd
    assert "libssl-dev" in cmd


def test_apt_adapter_normalize_ssl() -> None:
    from black_flag.adapters.ubuntu import AptAdapter

    adapter = AptAdapter()
    assert adapter.normalize_library_name("ssl-dev") == "libssl-dev"


def test_apt_adapter_normalize_unknown_raises() -> None:
    from black_flag.adapters.ubuntu import AptAdapter
    import pytest

    adapter = AptAdapter()
    with pytest.raises(KeyError):
        adapter.normalize_library_name("nonexistent-xyz")


def test_apt_adapter_query_package_known() -> None:
    from black_flag.adapters.ubuntu import AptAdapter

    adapter = AptAdapter()
    assert adapter.query_package("ssl-dev") is True


def test_apt_adapter_query_package_unknown() -> None:
    from black_flag.adapters.ubuntu import AptAdapter

    adapter = AptAdapter()
    assert adapter.query_package("totally-unknown-package-xyz") is False


def test_apt_adapter_environment_configuration() -> None:
    from black_flag.adapters.ubuntu import AptAdapter

    adapter = AptAdapter()
    env = adapter.environment_configuration()
    assert "DEBIAN_FRONTEND" in env
    assert env["DEBIAN_FRONTEND"] == "noninteractive"


def test_apt_adapter_verify_dependency() -> None:
    from black_flag.adapters.ubuntu import AptAdapter

    adapter = AptAdapter()
    cmd = adapter.verify_dependency("libssl-dev")
    assert "libssl-dev" in cmd


def test_apt_adapter_uninstall_dependency() -> None:
    from black_flag.adapters.ubuntu import AptAdapter

    adapter = AptAdapter()
    cmd = adapter.uninstall_dependency("libssl-dev")
    assert "apt-get" in cmd
    assert "libssl-dev" in cmd


def test_apt_adapter_distro_id() -> None:
    from black_flag.adapters.ubuntu import AptAdapter

    assert AptAdapter.distro_id == "ubuntu"
    assert AptAdapter.docker_image == "ubuntu:22.04"


# ---------------------------------------------------------------------------
# adapters: DnfAdapter
# ---------------------------------------------------------------------------


def test_dnf_adapter_package_manager() -> None:
    from black_flag.adapters.fedora import DnfAdapter

    adapter = DnfAdapter()
    assert adapter.package_manager() == "dnf"


def test_dnf_adapter_install_dependency() -> None:
    from black_flag.adapters.fedora import DnfAdapter

    adapter = DnfAdapter()
    cmd = adapter.install_dependency("openssl-devel")
    assert "dnf" in cmd
    assert "openssl-devel" in cmd


def test_dnf_adapter_normalize_ssl() -> None:
    from black_flag.adapters.fedora import DnfAdapter

    adapter = DnfAdapter()
    assert adapter.normalize_library_name("ssl-dev") == "openssl-devel"


def test_dnf_adapter_environment_configuration_empty() -> None:
    from black_flag.adapters.fedora import DnfAdapter

    adapter = DnfAdapter()
    # Fedora does not inject DEBIAN_FRONTEND
    assert "DEBIAN_FRONTEND" not in adapter.environment_configuration()


def test_dnf_adapter_verify_dependency() -> None:
    from black_flag.adapters.fedora import DnfAdapter

    adapter = DnfAdapter()
    cmd = adapter.verify_dependency("openssl-devel")
    assert "rpm" in cmd
    assert "openssl-devel" in cmd


def test_dnf_adapter_distro_id() -> None:
    from black_flag.adapters.fedora import DnfAdapter

    assert DnfAdapter.distro_id == "fedora"
    assert DnfAdapter.docker_image == "fedora:41"


# ---------------------------------------------------------------------------
# adapters: PacmanAdapter
# ---------------------------------------------------------------------------


def test_pacman_adapter_package_manager() -> None:
    from black_flag.adapters.arch import PacmanAdapter

    adapter = PacmanAdapter()
    assert adapter.package_manager() == "pacman"


def test_pacman_adapter_install_dependency() -> None:
    from black_flag.adapters.arch import PacmanAdapter

    adapter = PacmanAdapter()
    cmd = adapter.install_dependency("openssl")
    assert "pacman" in cmd
    assert "--noconfirm" in cmd
    assert "openssl" in cmd


def test_pacman_adapter_normalize_ssl() -> None:
    from black_flag.adapters.arch import PacmanAdapter

    adapter = PacmanAdapter()
    assert adapter.normalize_library_name("ssl-dev") == "openssl"


def test_pacman_adapter_verify_dependency() -> None:
    from black_flag.adapters.arch import PacmanAdapter

    adapter = PacmanAdapter()
    cmd = adapter.verify_dependency("openssl")
    assert "pacman" in cmd
    assert "openssl" in cmd


def test_pacman_adapter_distro_id() -> None:
    from black_flag.adapters.arch import PacmanAdapter

    assert PacmanAdapter.distro_id == "arch"
    assert PacmanAdapter.docker_image == "archlinux:latest"


# ---------------------------------------------------------------------------
# runtime/detector.py
# ---------------------------------------------------------------------------


def test_detect_distro_id_returns_string() -> None:
    from black_flag.runtime.detector import detect_distro_id

    distro_id = detect_distro_id()
    assert isinstance(distro_id, str)
    assert len(distro_id) > 0


def test_get_all_adapters_returns_three() -> None:
    from black_flag.runtime.detector import get_all_adapters

    adapters = get_all_adapters()
    assert len(adapters) == 3
    ids = {a.distro_id for a in adapters}
    assert ids == {"ubuntu", "fedora", "arch"}


def test_adapter_for_distro_ubuntu() -> None:
    from black_flag.runtime.detector import adapter_for_distro
    from black_flag.adapters.ubuntu import AptAdapter

    adapter = adapter_for_distro("ubuntu")
    assert adapter is not None
    assert isinstance(adapter, AptAdapter)


def test_adapter_for_distro_fedora() -> None:
    from black_flag.runtime.detector import adapter_for_distro
    from black_flag.adapters.fedora import DnfAdapter

    adapter = adapter_for_distro("fedora")
    assert adapter is not None
    assert isinstance(adapter, DnfAdapter)


def test_adapter_for_distro_arch() -> None:
    from black_flag.runtime.detector import adapter_for_distro
    from black_flag.adapters.arch import PacmanAdapter

    adapter = adapter_for_distro("arch")
    assert adapter is not None
    assert isinstance(adapter, PacmanAdapter)


def test_adapter_for_distro_unknown_returns_none() -> None:
    from black_flag.runtime.detector import adapter_for_distro

    assert adapter_for_distro("windows") is None
    assert adapter_for_distro("macos") is None
    assert adapter_for_distro("") is None


def test_detect_adapter_returns_adapter_or_none() -> None:
    from black_flag.runtime.detector import detect_adapter

    # On Windows/macOS (the dev machine), this returns None — that is correct.
    # On Ubuntu/Fedora/Arch, it returns the matching adapter.
    result = detect_adapter()
    if result is not None:
        assert result.distro_id in ("ubuntu", "fedora", "arch")


# ---------------------------------------------------------------------------
# demo app scaffold
# ---------------------------------------------------------------------------


def test_demo_app_file_exists() -> None:
    demo_app = os.path.join(
        os.path.dirname(__file__), "..", "examples", "portable-demo", "app.py"
    )
    assert os.path.exists(demo_app), "examples/portable-demo/app.py must exist"


def test_demo_setup_sh_exists() -> None:
    setup_sh = os.path.join(
        os.path.dirname(__file__), "..", "examples", "portable-demo", "setup.sh"
    )
    assert os.path.exists(setup_sh), "examples/portable-demo/setup.sh must exist"


def test_demo_app_contains_expected_flaws() -> None:
    """Verify that each deliberate flaw is still present (not accidentally fixed)."""
    demo_app = os.path.join(
        os.path.dirname(__file__), "..", "examples", "portable-demo", "app.py"
    )
    with open(demo_app) as f:
        content = f.read()

    assert "/etc/debian_version" in content, "Flaw 3 (hardcoded path) must be present"
    assert 'os.environ["DEBIAN_FRONTEND"]' in content, "Flaw 4 (env assumption) must be present"


def test_demo_setup_sh_contains_expected_flaws() -> None:
    """Verify that each deliberate flaw in setup.sh is still present."""
    setup_sh = os.path.join(
        os.path.dirname(__file__), "..", "examples", "portable-demo", "setup.sh"
    )
    with open(setup_sh) as f:
        content = f.read()

    assert "apt-get" in content, "Flaw 2 (apt-get call) must be present"
    assert "[[" in content, "Flaw 1 (bash-ism [[ ) must be present"
