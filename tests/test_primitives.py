"""
tests/test_primitives.py — Tests for all 7 adaptation primitives (Phase 2).

Each primitive is tested with:
  - matches() returns True on matching content
  - apply() returns modified content
  - apply() raises PrimitiveNotApplicable when pattern not found
"""
from __future__ import annotations

import pytest
from black_flag.primitives.base import PrimitiveNotApplicable


# ---------------------------------------------------------------------------
# PRIM-001: pkg_manager_call
# ---------------------------------------------------------------------------

def test_prim001_matches_apt_get():
    from black_flag.primitives.pkg_manager import PkgManagerCallPrimitive
    p = PkgManagerCallPrimitive()
    assert p.matches("apt-get install -y libssl-dev\n", ".sh")


def test_prim001_does_not_match_py_file():
    from black_flag.primitives.pkg_manager import PkgManagerCallPrimitive
    p = PkgManagerCallPrimitive()
    assert not p.matches("import os\n", ".py")


def test_prim001_apply_replaces_apt_get():
    from black_flag.primitives.pkg_manager import PkgManagerCallPrimitive
    p = PkgManagerCallPrimitive()
    content = "#!/usr/bin/env bash\napt-get install -y libssl-dev\n"
    result = p.apply(content, {}, {})
    assert "apt-get" in result  # Ubuntu branch still has apt-get
    assert "dnf install -y" in result
    assert "pacman -S --noconfirm" in result
    assert "_BF_DISTRO" in result
    assert "case" in result


def test_prim001_raises_when_no_match():
    from black_flag.primitives.pkg_manager import PkgManagerCallPrimitive
    p = PkgManagerCallPrimitive()
    with pytest.raises(PrimitiveNotApplicable):
        p.apply("echo hello\n", {}, {})


def test_prim001_package_normalization():
    """libssl-dev (ubuntu) should become openssl-devel on fedora branch."""
    from black_flag.primitives.pkg_manager import PkgManagerCallPrimitive
    p = PkgManagerCallPrimitive()
    content = "apt-get install -y libssl-dev\n"
    result = p.apply(content, {}, {})
    assert "openssl-devel" in result    # fedora name
    assert "openssl" in result          # arch name


# ---------------------------------------------------------------------------
# PRIM-002: distro_path_check
# ---------------------------------------------------------------------------

def test_prim002_matches_os_path_exists():
    from black_flag.primitives.path_normalize import DistroPpathCheckPrimitive
    p = DistroPpathCheckPrimitive()
    content = 'if os.path.exists("/etc/debian_version"):\n    pass\n'
    assert p.matches(content, ".py")


def test_prim002_apply_replaces_check():
    from black_flag.primitives.path_normalize import DistroPpathCheckPrimitive
    p = DistroPpathCheckPrimitive()
    content = (
        'import os\n'
        'if os.path.exists("/etc/debian_version"):\n'
        '    print("debian")\n'
        'else:\n'
        '    raise RuntimeError("This application requires a Debian-based system.")\n'
    )
    result = p.apply(content, {}, {})
    assert '_bf_detect_distro' in result
    assert '/etc/debian_version' not in result


def test_prim002_raises_when_no_match():
    from black_flag.primitives.path_normalize import DistroPpathCheckPrimitive
    p = DistroPpathCheckPrimitive()
    with pytest.raises(PrimitiveNotApplicable):
        p.apply("import os\nprint('hello')\n", {}, {})


# ---------------------------------------------------------------------------
# PRIM-003: env_var_portability
# ---------------------------------------------------------------------------

def test_prim003_matches_environ_subscript():
    from black_flag.primitives.env_portability import EnvVarPortabilityPrimitive
    p = EnvVarPortabilityPrimitive()
    assert p.matches('frontend = os.environ["DEBIAN_FRONTEND"]\n', ".py")


def test_prim003_apply_replaces_subscript():
    from black_flag.primitives.env_portability import EnvVarPortabilityPrimitive
    p = EnvVarPortabilityPrimitive()
    content = 'frontend = os.environ["DEBIAN_FRONTEND"]\n'
    result = p.apply(content, {}, {})
    assert 'os.environ.get(' in result
    assert '"DEBIAN_FRONTEND"' in result
    assert '"noninteractive"' in result


def test_prim003_raises_when_no_match():
    from black_flag.primitives.env_portability import EnvVarPortabilityPrimitive
    p = EnvVarPortabilityPrimitive()
    with pytest.raises(PrimitiveNotApplicable):
        p.apply('x = os.environ.get("PATH", "")\n', {}, {})


# ---------------------------------------------------------------------------
# PRIM-004: shell_compat
# ---------------------------------------------------------------------------

def test_prim004_matches_bin_bash():
    from black_flag.primitives.shell_compat import ShellCompatPrimitive
    p = ShellCompatPrimitive()
    assert p.matches("#!/bin/bash\necho hello\n", ".sh")


def test_prim004_explicit_bash_strategy():
    from black_flag.primitives.shell_compat import ShellCompatPrimitive
    p = ShellCompatPrimitive()
    content = "#!/bin/bash\necho hello\n"
    result = p.apply(content, {"strategy": "explicit_bash"}, {})
    assert result.startswith("#!/usr/bin/env bash")
    assert "#!/bin/bash" not in result


def test_prim004_posix_convert_strategy():
    from black_flag.primitives.shell_compat import ShellCompatPrimitive
    p = ShellCompatPrimitive()
    content = '#!/usr/bin/env bash\nif [[ -z "$VAR" ]]; then echo hi; fi\n'
    result = p.apply(content, {"strategy": "posix_convert"}, {})
    assert "[[ " not in result
    assert '[ -z' in result


def test_prim004_raises_explicit_bash_when_no_shebang():
    from black_flag.primitives.shell_compat import ShellCompatPrimitive
    p = ShellCompatPrimitive()
    with pytest.raises(PrimitiveNotApplicable):
        p.apply("#!/usr/bin/env bash\necho hello\n", {"strategy": "explicit_bash"}, {})


def test_prim004_raises_posix_convert_when_no_double_bracket():
    from black_flag.primitives.shell_compat import ShellCompatPrimitive
    p = ShellCompatPrimitive()
    with pytest.raises(PrimitiveNotApplicable):
        p.apply("#!/bin/bash\nif [ -z \"$X\" ]; then echo hi; fi\n", {"strategy": "posix_convert"}, {})


# ---------------------------------------------------------------------------
# PRIM-006: requirements_normalize
# ---------------------------------------------------------------------------

def test_prim006_matches_debian_package_in_requirements():
    from black_flag.primitives.requirements_norm import RequirementsNormPrimitive
    p = RequirementsNormPrimitive()
    assert p.matches("python3-cryptography\nrequests\n", ".txt")


def test_prim006_apply_replaces_debian_name():
    from black_flag.primitives.requirements_norm import RequirementsNormPrimitive
    p = RequirementsNormPrimitive()
    content = "python3-cryptography\nrequests\n"
    result = p.apply(content, {}, {})
    assert "python3-cryptography" not in result
    assert "cryptography" in result
    assert "requests" in result  # other entries unchanged


def test_prim006_raises_when_no_match():
    from black_flag.primitives.requirements_norm import RequirementsNormPrimitive
    p = RequirementsNormPrimitive()
    with pytest.raises(PrimitiveNotApplicable):
        p.apply("requests\nflask\nnumpy\n", {}, {})


# ---------------------------------------------------------------------------
# Catalog
# ---------------------------------------------------------------------------

def test_catalog_contains_all_7_primitives():
    from black_flag.primitives.catalog import list_primitive_ids
    ids = list_primitive_ids()
    expected = [
        "pkg_manager_call",
        "distro_path_check",
        "env_var_portability",
        "shell_compat",
        "package_name_remap",
        "requirements_normalize",
        "service_name_remap",
    ]
    for expected_id in expected:
        assert expected_id in ids, f"Missing primitive: {expected_id}"


def test_catalog_get_primitive():
    from black_flag.primitives.catalog import get_primitive
    p = get_primitive("pkg_manager_call")
    assert p.id == "pkg_manager_call"


def test_catalog_get_unknown_raises():
    from black_flag.primitives.catalog import get_primitive
    with pytest.raises(KeyError):
        get_primitive("nonexistent_primitive")


def test_catalog_primitive_for_category():
    from black_flag.primitives.catalog import primitive_for_category
    assert primitive_for_category("package-manager") == "pkg_manager_call"
    assert primitive_for_category("hardcoded-path") == "distro_path_check"
    assert primitive_for_category("env-assumption") == "env_var_portability"
    assert primitive_for_category("shell-ism") == "shell_compat"
    assert primitive_for_category("service-name") == "service_name_remap"
    assert primitive_for_category("unknown") is None
