"""
primitives/catalog.py — PrimitiveCatalog: registry of all adaptation primitives.

All 7 primitives are registered here by their string ID.
The planner and patcher reference primitives exclusively through the catalog
so that adding a new primitive requires only one change in this file.
"""
from __future__ import annotations

from .base import AdaptationPrimitive
from .pkg_manager import PkgManagerCallPrimitive
from .path_normalize import DistroPpathCheckPrimitive
from .env_portability import EnvVarPortabilityPrimitive
from .shell_compat import ShellCompatPrimitive
from .pkg_name_remap import PackageNameRemapPrimitive
from .requirements_norm import RequirementsNormPrimitive
from .service_names import ServiceNameRemapPrimitive

# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

_PRIMITIVES: list[AdaptationPrimitive] = [
    PkgManagerCallPrimitive(),       # PRIM-001
    DistroPpathCheckPrimitive(),     # PRIM-002
    EnvVarPortabilityPrimitive(),    # PRIM-003
    ShellCompatPrimitive(),          # PRIM-004
    PackageNameRemapPrimitive(),     # PRIM-005
    RequirementsNormPrimitive(),     # PRIM-006
    ServiceNameRemapPrimitive(),     # PRIM-007
]

_CATALOG: dict[str, AdaptationPrimitive] = {p.id: p for p in _PRIMITIVES}


def get_primitive(primitive_id: str) -> AdaptationPrimitive:
    """Return the primitive with the given ID. Raises KeyError if not found."""
    if primitive_id not in _CATALOG:
        raise KeyError(
            f"Unknown primitive ID: {primitive_id!r}. "
            f"Valid IDs: {list(_CATALOG.keys())}"
        )
    return _CATALOG[primitive_id]


def list_primitive_ids() -> list[str]:
    """Return all registered primitive IDs in registration order."""
    return [p.id for p in _PRIMITIVES]


def list_primitives() -> list[AdaptationPrimitive]:
    """Return all registered primitive instances."""
    return list(_PRIMITIVES)


def primitive_for_category(category: str) -> str | None:
    """
    Return the default primitive ID for a given issue category.
    Used by the deterministic fallback provider.
    """
    _CATEGORY_MAP: dict[str, str] = {
        "package-manager": "pkg_manager_call",
        "library-name": "package_name_remap",
        "hardcoded-path": "distro_path_check",
        "env-assumption": "env_var_portability",
        "shell-ism": "shell_compat",
        "service-name": "service_name_remap",
    }
    return _CATEGORY_MAP.get(category)
