"""
black_flag.build — Docker build matrix and script generation.

Submodules:
  container   — Docker availability detection and container execution with host-volume mounting.
  script_gen  — Generate per-distro prepare.sh / build.sh / test.sh stage scripts.
  matrix      — Run the 3×3 (distro × stage) build/test matrix and aggregate results.
"""
