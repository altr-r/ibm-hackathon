"""
build/container.py — Docker container execution for the Black Flag build matrix.

Responsibilities:
  - Detect whether the Docker daemon is available and reachable from the host.
  - Run a single PREPARE / BUILD / TEST stage inside a target Docker container.
  - Mount the working tree into the container at /src using a cross-platform
    path conversion that works on:
        * Linux hosts (direct bind mount)
        * macOS hosts (direct bind mount)
        * Windows + Docker Desktop (drive-letter path translation for WSL2 backend)
  - Execute the generated stage script (from bf_scripts/<distro>/<stage>.sh)
    and capture stdout / stderr / exit_code / wall-clock time.

This module NEVER executes application code directly on the host — only inside
containers.  All distro-specific commands live inside the generated stage
scripts (build/script_gen.py) so this file remains fully cross-platform.
"""
from __future__ import annotations

import shutil
import subprocess
import time
from pathlib import Path
from typing import Callable

from black_flag.core.types import StageResult


# ---------------------------------------------------------------------------
# Public: Docker availability probe
# ---------------------------------------------------------------------------

def is_docker_available() -> bool:
    """
    Return True when the Docker CLI is installed and the daemon is reachable.

    Runs ``docker info`` with a short timeout — both the command itself and
    the daemon handshake must succeed within 5 seconds.
    """
    docker = shutil.which("docker")
    if docker is None:
        return False
    try:
        result = subprocess.run(
            [docker, "info", "--format", "{{.ServerVersion}}"],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0 and result.stdout.strip() != ""


# ---------------------------------------------------------------------------
# Private: path translation for Windows + Docker Desktop
# ---------------------------------------------------------------------------

def _bind_mount_source(host_path: Path) -> str:
    """
    Convert *host_path* (an absolute, resolved Path) into a string suitable
    for the left-hand side of a Docker ``--volume`` bind-mount argument.

    On POSIX systems this is ``str(host_path)``.  On Windows the path is
    normalised to forward slashes and the drive letter is lower-cased
    (``E:\foo\bar`` → ``//e/foo/bar``).  This matches the convention that
    Docker Desktop uses for WSL2 / Hyper-V backends.  Docker Desktop still
    accepts native Windows paths when invoked from a Windows docker.exe but
    the slash form is recognised by *both* docker.exe and by the WSL docker
    client, which is important for mixed development environments.
    """
    resolved = host_path.resolve()
    s = str(resolved)

    # --- Windows drive-letter case: ``E:\foo\bar`` ---------------------------
    if len(s) >= 2 and s[1] == ":" and s[0].isalpha():
        drive = s[0].lower()
        tail = s[2:].replace("\\", "/")
        return f"//{drive}{tail}"

    # --- POSIX / already forward-slash form ---------------------------------
    return s.replace("\\", "/")


# ---------------------------------------------------------------------------
# Public: run a single stage in a container
# ---------------------------------------------------------------------------

def run_stage(
    *,
    working_tree: Path,
    distro_id: str,
    stage: str,
    docker_image: str,
    env: dict[str, str] | None = None,
) -> StageResult:
    """
    Run *stage* (``"prepare"``, ``"build"`` or ``"test"``) for *distro_id*
    inside a container using *docker_image*.

    The working tree is mounted read/write at ``/src`` inside the container
    (stage scripts install system packages into the container image, but
    Python venvs and build artefacts live on the mounted volume so they are
    preserved across stages for the same distro).

    Returns a :class:`StageResult` populated with captured stdout, stderr,
    exit code, and wall-clock time.
    """
    host_scripts = _bind_mount_source(working_tree)
    container_mount = "/src"
    script_path = f"{container_mount}/bf_scripts/{distro_id}/{stage}.sh"

    docker_args = [
        "docker",
        "run",
        "--rm",
        "--volume", f"{host_scripts}:{container_mount}",
        "--workdir", container_mount,
    ]

    # Inject environment variables requested by the distro adapter
    for key, value in (env or {}).items():
        docker_args.extend(["-e", f"{key}={value}"])

    docker_args.extend([
        docker_image,
        "bash",
        "-euo", "pipefail",
        "-c",
        (
            f"if [ -f '{script_path}' ]; then "
            f"  bash '{script_path}'; "
            f"else "
            f"  echo 'black-flag: no script for {distro_id}/{stage}' >&2; "
            f"  exit 0; "
            f"fi"
        ),
    ])

    start = time.perf_counter()
    try:
        completed = subprocess.run(
            docker_args,
            capture_output=True,
            text=True,
            timeout=600,
        )
        exit_code = completed.returncode
        stdout = completed.stdout
        stderr = completed.stderr
    except subprocess.TimeoutExpired as exc:
        exit_code = 124
        stdout = exc.stdout.decode("utf-8", errors="replace") if isinstance(exc.stdout, (bytes, bytearray)) else (exc.stdout or "")
        stderr = (exc.stderr.decode("utf-8", errors="replace") if isinstance(exc.stderr, (bytes, bytearray)) else (exc.stderr or "")) + f"\n\n[black-flag] timeout after 600s for {distro_id}/{stage}"
    except OSError as exc:
        exit_code = -1
        stdout = ""
        stderr = f"[black-flag] failed to launch docker for {distro_id}/{stage}: {exc}"

    elapsed = time.perf_counter() - start

    return StageResult(
        distro=distro_id,
        stage=stage,
        exit_code=exit_code,
        stdout=stdout,
        stderr=stderr,
        elapsed_s=round(elapsed, 2),
    )


# ---------------------------------------------------------------------------
# Public: run all 3 stages for one distro in a single persistent container
# ---------------------------------------------------------------------------

def run_distro_stages(
    *,
    working_tree: Path,
    distro_id: str,
    docker_image: str,
    env: dict[str, str] | None = None,
) -> tuple[StageResult, StageResult, StageResult]:
    """
    Run ``prepare`` → ``build`` → ``test`` sequentially inside **one** Docker
    container for *distro_id*.

    Running all three stages inside a single container is required for
    correctness because:

    * PREPARE installs OS packages (including the application's own
      ``setup.sh``) into the container's ephemeral filesystem.
    * BUILD installs pip packages likewise.
    * If each stage used a separate ``docker run --rm``, everything installed
      in PREPARE and BUILD would be discarded before TEST ran — producing
      spurious TEST failures even when the adaptation was correct.

    To produce three distinct :class:`StageResult` objects (rather than one
    monolithic blob), the bash driver inside the container writes each
    stage's stdout / stderr / exit-code / elapsed-seconds into a per-stage
    directory under ``/src/.bf_stage_outputs/``, which is the mounted host
    working tree.  After the container exits, the host parses those files
    into three :class:`StageResult` tuples and deletes the scratch folder.

    Stages short-circuit on failure (BUILD skipped if PREPARE fails, TEST
    skipped if BUILD fails).  Skipped stages carry ``exit_code = -1`` with a
    sentinel stderr message so the CLI matrix display can render them as
    an em-dash.
    """
    host_src = _bind_mount_source(working_tree)
    mount = "/src"
    scratch_rel = ".bf_stage_outputs"
    scratch_host = working_tree / scratch_rel
    # Clean any previous run residue
    import shutil as _shutil
    if scratch_host.exists():
        _shutil.rmtree(scratch_host, ignore_errors=True)
    scratch_host.mkdir(parents=True, exist_ok=False)

    docker_args = [
        "docker",
        "run",
        "--rm",
        "--volume", f"{host_src}:{mount}",
        "--workdir", mount,
    ]
    for key, value in (env or {}).items():
        docker_args.extend(["-e", f"{key}={value}"])

    driver = (
        f"cd {mount}\n"
        f"OUT={mount}/{scratch_rel}\n"
        f"mkdir -p \"$OUT\"\n"
        f"run_stage() {{\n"
        f"  local name=$1; local doit=$2\n"
        f"  mkdir -p \"$OUT/$name\"\n"
        f"  if [ \"$doit\" != \"1\" ]; then\n"
        f"    echo -1 > \"$OUT/$name/rc\"\n"
        f"    echo 0.0 > \"$OUT/$name/elapsed\"\n"
        f"    : > \"$OUT/$name/stdout\"\n"
        f"    printf '[black-flag] skipped (previous stage failed)\\n' > \"$OUT/$name/stderr\"\n"
        f"    return 0\n"
        f"  fi\n"
        f"  local t0=${{SECONDS:-0}} t1\n"
        f"  if [ -f \"{mount}/bf_scripts/{distro_id}/$name.sh\" ]; then\n"
        f"    bash \"{mount}/bf_scripts/{distro_id}/$name.sh\" \\\n"
        f"      >\"$OUT/$name/stdout\" 2>\"$OUT/$name/stderr\"\n"
        f"    local rc=$?\n"
        f"  else\n"
        f"    local rc=0\n"
        f"    : > \"$OUT/$name/stdout\"\n"
        f"    echo 'black-flag: no script for {distro_id}/$name' > \"$OUT/$name/stderr\"\n"
        f"  fi\n"
        f"  t1=${{SECONDS:-0}}\n"
        f"  echo \"$rc\" > \"$OUT/$name/rc\"\n"
        f"  awk -v a=\"$t0\" -v b=\"$t1\" 'BEGIN{{printf \"%.2f\\n\", (b-a)+0}}' > \"$OUT/$name/elapsed\"\n"
        f"  return 0\n"
        f"}}\n"
        f"rc=0\n"
        f"run_stage prepare 1\n"
        f"read rc < \"$OUT/prepare/rc\" || rc=1\n"
        f"[ \"$rc\" = \"0\" ] && doit=1 || doit=0\n"
        f"run_stage build $doit\n"
        f"read rc < \"$OUT/build/rc\" || rc=1\n"
        f"[ \"$rc\" = \"0\" ] && doit=1 || doit=0\n"
        f"run_stage test $doit\n"
        f"exit 0\n"
    )

    docker_args.extend([
        docker_image,
        "bash",
        "-euo", "pipefail",
        "-c", driver,
    ])

    overall_start = time.perf_counter()
    try:
        completed = subprocess.run(
            docker_args,
            capture_output=True,
            text=True,
            timeout=1800,
        )
        launch_error = None
        launch_rc = completed.returncode
    except subprocess.TimeoutExpired as exc:
        launch_error = "timeout after 1800s"
        launch_rc = 124
    except OSError as exc:
        launch_error = f"failed to launch docker: {exc}"
        launch_rc = -1
    overall_elapsed = time.perf_counter() - overall_start

    def _parse(stage_name: str, fallback_stderr: str) -> StageResult:
        sd = scratch_host / stage_name
        try:
            rc_s = (sd / "rc").read_text(encoding="utf-8").strip()
            rc = int(rc_s) if rc_s else -1
        except (OSError, ValueError):
            rc = -1
        try:
            el_s = (sd / "elapsed").read_text(encoding="utf-8").strip()
            elapsed = float(el_s) if el_s else 0.0
        except (OSError, ValueError):
            elapsed = overall_elapsed / 3.0
        try:
            stdout = (sd / "stdout").read_text(encoding="utf-8")
        except OSError:
            stdout = ""
        try:
            stderr_txt = (sd / "stderr").read_text(encoding="utf-8")
        except OSError:
            stderr_txt = ""
        if launch_error and rc != 0 and rc != -1:
            stderr_txt = (stderr_txt + "\n" if stderr_txt else "") + f"[black-flag] {launch_error}\n"
        if rc == -1 and not stderr_txt:
            stderr_txt = fallback_stderr
        return StageResult(
            distro=distro_id,
            stage=stage_name,
            exit_code=rc,
            stdout=stdout,
            stderr=stderr_txt,
            elapsed_s=round(elapsed, 2),
        )

    # If docker itself failed (script file never created), produce FAILED results
    if launch_rc != 0 and not (scratch_host / "prepare" / "rc").exists():
        msg = (
            f"[black-flag] docker for {distro_id} exited {launch_rc}"
            + (f": {launch_error}" if launch_error else "")
            + "\n"
        )
        try:
            msg += f"docker-stderr:\n{completed.stderr}"
        except Exception:
            pass
        results = []
        for i, sname in enumerate(("prepare", "build", "test")):
            results.append(StageResult(
                distro=distro_id,
                stage=sname,
                exit_code=1 if i == 0 else -1,
                stdout=completed.stdout if i == 0 else "",
                stderr=msg if i == 0 else f"[black-flag] skipped ({distro_id}/prepare failed)",
                elapsed_s=round(overall_elapsed, 2) if i == 0 else 0.0,
            ))
        _shutil.rmtree(scratch_host, ignore_errors=True)
        return tuple(results)

    prepare_sr = _parse("prepare", f"[black-flag] {distro_id}/prepare produced no result files")
    build_sr = _parse("build", f"[black-flag] skipped (previous stage for {distro_id} failed)")
    test_sr = _parse("test", f"[black-flag] skipped (previous stage for {distro_id} failed)")

    _shutil.rmtree(scratch_host, ignore_errors=True)
    return prepare_sr, build_sr, test_sr
