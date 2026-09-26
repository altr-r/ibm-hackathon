"""
primitives/requirements_norm.py — PRIM-006: requirements_normalize

Replaces Debian-style package names (e.g. python3-cryptography) in a
requirements.txt file with the correct PyPI package names.

Example:
  Before: python3-cryptography
  After:  cryptography

The mapping is maintained in _DEBIAN_TO_PYPI below. The normalization table
in adapters/normalization.py covers system packages; this covers PyPI confusion.
"""
from __future__ import annotations

import re

from .base import AdaptationPrimitive, PrimitiveNotApplicable

# Mapping of common Debian package names that appear in requirements.txt by mistake
_DEBIAN_TO_PYPI: dict[str, str] = {
    "python3-cryptography": "cryptography",
    "python3-requests": "requests",
    "python3-flask": "flask",
    "python3-django": "django",
    "python3-numpy": "numpy",
    "python3-pandas": "pandas",
    "python3-scipy": "scipy",
    "python3-matplotlib": "matplotlib",
    "python3-yaml": "pyyaml",
    "python3-toml": "toml",
    "python3-setuptools": "setuptools",
    "python3-pip": "pip",
    "python3-boto3": "boto3",
    "python3-paramiko": "paramiko",
    "python3-sqlalchemy": "sqlalchemy",
    "python3-psycopg2": "psycopg2",
    "python3-redis": "redis",
    "python3-celery": "celery",
    "python-cryptography": "cryptography",
    "python-requests": "requests",
}


class RequirementsNormPrimitive(AdaptationPrimitive):
    id = "requirements_normalize"
    description = "Replace Debian-style package names in requirements.txt with PyPI names"
    supported_file_types = [".txt"]
    safe_to_auto_apply = True

    def matches(self, file_content: str, file_ext: str) -> bool:
        if file_ext not in self.supported_file_types:
            return False
        return any(name in file_content for name in _DEBIAN_TO_PYPI)

    def apply(self, file_content: str, params: dict, adapters: dict) -> str:
        old_entry = params.get("old_entry", "")
        new_entry = params.get("new_entry", "")

        if old_entry and new_entry:
            if old_entry not in file_content:
                raise PrimitiveNotApplicable(
                    f"requirements_normalize: '{old_entry}' not found in requirements.txt"
                )
            return file_content.replace(old_entry, new_entry)

        # Auto-mode: replace all known Debian package names
        result = file_content
        changed = False
        for debian_name, pypi_name in _DEBIAN_TO_PYPI.items():
            if debian_name in result:
                result = re.sub(
                    r"^" + re.escape(debian_name) + r"(\s*(?:#.*)?)?$",
                    pypi_name,
                    result,
                    flags=re.MULTILINE,
                )
                changed = True

        if not changed:
            raise PrimitiveNotApplicable(
                "requirements_normalize: no Debian-style package names found"
            )
        return result
