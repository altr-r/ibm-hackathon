"""
installer/installer.py — Install a .bfpack file on the host system.

Installation flow:
  1. Extract .bfpack to ~/.local/share/bfpack/<name>/
  2. Read manifest.json to get name, version, targets
  3. Detect host distro via runtime/detector.py
  4. Verify the target is listed as COMPATIBLE in the manifest
  5. Run bf_scripts/<distro>/prepare.sh  (install system deps)
  6. Run bf_scripts/<distro>/build.sh    (build from source)
  7. Run bf_scripts/<distro>/test.sh     (smoke test)
  8. Symlink source/app.py (or main entry) → ~/.local/bin/<name>
  9. Print success / failure

The installer does NOT use Docker — it runs directly on the host.
The host must be one of the supported distros.
"""
from __future__ import annotations

import os
import subprocess
import tarfile
from pathlib import Path

from black_flag.core.manifest import read_manifest, write_manifest
from black_flag.runtime.detector import detect_distro_id, adapter_for_distro


_INSTALL_BASE = Path.home() / ".local" / "share" / "bfpack"
_BIN_DIR = Path.home() / ".local" / "bin"


class InstallError(Exception):
    """Raised when installation fails at any step."""


def install_bfpack(bfpack_path: Path, force: bool = False) -> dict:
    """
    Install a .bfpack file.

    Returns a dict with keys:
      name, version, distro, install_dir, entry_point, status, message
    """
    if not bfpack_path.exists():
        raise InstallError(f"Package not found: {bfpack_path}")

    # --- Step 1: Extract ---
    with tarfile.open(bfpack_path, "r:gz") as tar:
        # Peek at manifest to get the package name
        try:
            manifest_member = tar.getmember("source/manifest.json")
        except KeyError:
            raise InstallError("Invalid .bfpack: missing source/manifest.json")

        import json
        manifest_data = json.loads(tar.extractfile(manifest_member).read().decode())
        pkg_name = manifest_data.get("name", bfpack_path.stem)
        pkg_version = manifest_data.get("version", "0.0.0")

        install_dir = _INSTALL_BASE / pkg_name
        if install_dir.exists():
            if force:
                import shutil
                shutil.rmtree(install_dir)
            else:
                raise InstallError(
                    f"{pkg_name} is already installed at {install_dir}. "
                    "Use --force to reinstall."
                )

        install_dir.mkdir(parents=True, exist_ok=True)

        # Extract only the source/ subtree
        for member in tar.getmembers():
            if member.name.startswith("source/"):
                member.name = member.name[len("source/"):]
                tar.extract(member, path=install_dir)

    # --- Step 2: Read manifest ---
    manifest_path = install_dir / "manifest.json"
    if not manifest_path.exists():
        raise InstallError("Extracted .bfpack does not contain manifest.json")
    manifest = read_manifest(manifest_path)

    # --- Step 3: Detect distro ---
    distro_id = detect_distro_id()
    adapter = adapter_for_distro(distro_id)
    if adapter is None:
        raise InstallError(
            f"Host distro '{distro_id}' is not a supported Black Flag target. "
            "Supported: ubuntu, fedora, arch."
        )

    # --- Step 4: Verify compatibility ---
    target_result = manifest.targets.get(distro_id)
    if target_result and target_result.status == "UNSUPPORTED":
        raise InstallError(
            f"{pkg_name} is marked UNSUPPORTED for {distro_id}. "
            "See the manifest for details."
        )

    # --- Steps 5-7: Run stage scripts ---
    scripts_dir = install_dir / "bf_scripts" / distro_id

    for stage in ("prepare", "build", "test"):
        script = scripts_dir / f"{stage}.sh"
        if not script.exists():
            # If scripts dir doesn't exist, skip gracefully
            continue
        result = subprocess.run(
            ["bash", str(script)],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            raise InstallError(
                f"Installation failed at {stage} stage:\n"
                f"stdout: {result.stdout[-500:]}\n"
                f"stderr: {result.stderr[-500:]}"
            )

    # --- Step 8: Create entry point symlink ---
    _BIN_DIR.mkdir(parents=True, exist_ok=True)
    entry_point = _find_entry_point(install_dir, pkg_name)
    symlink = _BIN_DIR / pkg_name
    if symlink.exists() or symlink.is_symlink():
        symlink.unlink()
    symlink.symlink_to(entry_point)

    return {
        "name": pkg_name,
        "version": pkg_version,
        "distro": distro_id,
        "install_dir": str(install_dir),
        "entry_point": str(entry_point),
        "status": "success",
        "message": f"{pkg_name} {pkg_version} installed successfully on {distro_id}",
    }


def list_installed() -> list[dict]:
    """Return metadata for all installed Black Flag packages."""
    if not _INSTALL_BASE.exists():
        return []
    packages = []
    for pkg_dir in sorted(_INSTALL_BASE.iterdir()):
        manifest_path = pkg_dir / "manifest.json"
        if manifest_path.exists():
            try:
                m = read_manifest(manifest_path)
                packages.append({
                    "name": m.name,
                    "version": m.version,
                    "score": m.portability_score,
                    "targets": {k: v.status for k, v in m.targets.items()},
                    "install_dir": str(pkg_dir),
                    "created_at": m.created_at,
                })
            except Exception:
                packages.append({"name": pkg_dir.name, "error": "Could not read manifest"})
    return packages


def _find_entry_point(install_dir: Path, pkg_name: str) -> Path:
    """Heuristic: find the main executable in the install directory."""
    for candidate in [
        install_dir / "app.py",
        install_dir / "main.py",
        install_dir / f"{pkg_name}.py",
    ]:
        if candidate.exists():
            return candidate
    # Fall back to any .py file
    for f in sorted(install_dir.glob("*.py")):
        return f
    return install_dir
