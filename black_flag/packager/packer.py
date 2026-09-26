"""
packager/packer.py — Create a .bfpack archive from the working tree.

.bfpack is a gzip-compressed tar archive containing:
  source/          — the patched working tree
  diffs/           — unified patch files for each applied primitive
  bf_scripts/      — generated stage scripts per distro
  manifest.json    — the full compatibility manifest

The archive is written to <output_dir>/<name>-<version>.bfpack.
"""
from __future__ import annotations

import tarfile
from pathlib import Path

from black_flag.core.types import CompatibilityManifest
from black_flag.core.manifest import write_manifest


def create_bfpack(
    *,
    working_tree: Path,
    manifest: CompatibilityManifest,
    output_dir: Path,
) -> Path:
    """
    Package *working_tree* into a .bfpack file.

    Returns the path to the created archive.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    archive_name = f"{manifest.name}-{manifest.version}.bfpack"
    archive_path = output_dir / archive_name

    # Write manifest.json into the working tree before archiving
    manifest_path = working_tree / "manifest.json"
    write_manifest(manifest, manifest_path)

    with tarfile.open(archive_path, "w:gz") as tar:
        # source/ — the entire working tree (excluding the archive itself)
        for item in sorted(working_tree.rglob("*")):
            if not item.is_file():
                continue
            # Skip the archive itself if it ends up in the tree
            if item == archive_path:
                continue
            rel = item.relative_to(working_tree)
            arcname = f"source/{rel}"
            tar.add(str(item), arcname=arcname)

    return archive_path
