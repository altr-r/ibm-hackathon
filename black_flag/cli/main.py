"""
cli/main.py — Black Flag CLI entry point.

Commands:
  adapt     — Full portability adaptation loop (analyze → plan → patch → build → test → package)
  analyze   — Static analysis only; print issue table and portability score
  install   — Install a .bfpack file on the host system
  status    — Show installed Black Flag packages
  info      — Display environment info (Docker, adapters, host distro)
"""
from __future__ import annotations

import shutil
import sys
import tempfile
from pathlib import Path
from typing import Annotated, Optional

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.progress import Progress, SpinnerColumn, TextColumn
from rich import box

app = typer.Typer(
    name="black-flag",
    help=(
        "AI-assisted Linux portability engine.\n\n"
        "Analyzes, adapts, builds, and tests applications across "
        "Ubuntu, Fedora, and Arch Linux."
    ),
    add_completion=False,
    rich_markup_mode="rich",
)

import sys as _sys
import io as _io
# Force UTF-8 on Windows to support emoji in rich output
if _sys.platform == "win32":
    try:
        _sys.stdout = _io.TextIOWrapper(_sys.stdout.buffer, encoding="utf-8", errors="replace")
        _sys.stderr = _io.TextIOWrapper(_sys.stderr.buffer, encoding="utf-8", errors="replace")
    except Exception:
        pass

console = Console()

# ---------------------------------------------------------------------------
# Version callback
# ---------------------------------------------------------------------------

_VERSION = "0.1.0"


def _version_callback(value: bool) -> None:
    if value:
        typer.echo(f"black-flag {_VERSION}")
        raise typer.Exit()


@app.callback()
def main(
    version: Annotated[
        Optional[bool],
        typer.Option(
            "--version", "-V",
            callback=_version_callback,
            is_eager=True,
            help="Show version and exit.",
        ),
    ] = None,
) -> None:
    """Black Flag — AI-assisted Linux portability engine."""


# ---------------------------------------------------------------------------
# Shared display helpers
# ---------------------------------------------------------------------------

def _score_indicator(score: float) -> str:
    if score >= 0.90:
        return f"[green]{score:.2f}[/green] ✅"
    elif score >= 0.70:
        return f"[green]{score:.2f}[/green] 🟢"
    elif score >= 0.40:
        return f"[yellow]{score:.2f}[/yellow] 🟡"
    else:
        return f"[red]{score:.2f}[/red] 🔴"


def _print_issue_table(issues: list, score: float) -> None:
    """Print a rich table of portability issues."""
    from black_flag.core.types import PortabilityIssue

    if not issues:
        console.print("  [green]✅ No portability issues detected.[/green]")
        return

    table = Table(
        title=f"Portability Issues  (score: {_score_indicator(score)})",
        box=box.SIMPLE_HEAVY,
        show_lines=False,
    )
    table.add_column("Sev", width=5)
    table.add_column("Category", min_width=16)
    table.add_column("File:Line", min_width=22)
    table.add_column("Affected", min_width=12)
    table.add_column("Explanation", min_width=40)
    table.add_column("Primitive", min_width=18)

    sev_color = {"error": "red", "warning": "yellow", "info": "cyan"}

    for issue in issues:
        c = sev_color.get(issue.severity, "white")
        table.add_row(
            f"[{c}]{issue.severity[:4]}[/{c}]",
            issue.category,
            f"{issue.source_file}:{issue.line}",
            ", ".join(issue.affected_targets),
            issue.explanation[:60] + ("…" if len(issue.explanation) > 60 else ""),
            issue.suggested_primitive or "—",
        )

    console.print(table)


def _print_matrix_table(matrix, targets: list[str]) -> None:
    """Print the build/test matrix as a rich table."""
    table = Table(title="Build / Test Matrix", box=box.SIMPLE_HEAVY)
    table.add_column("Target", style="bold")
    table.add_column("PREPARE", justify="center")
    table.add_column("BUILD", justify="center")
    table.add_column("TEST", justify="center")
    table.add_column("Result", justify="center")

    def _cell(results, distro: str, stage: str) -> str:
        for r in results:
            if r.distro == distro and r.stage == stage:
                if r.exit_code == 0:
                    return f"[green]✅[/green] ({r.elapsed_s:.1f}s)"
                elif r.exit_code == -1:
                    return "[dim]—[/dim]"
                else:
                    return f"[red]❌[/red] ({r.elapsed_s:.1f}s)"
        return "[dim]—[/dim]"

    for distro in targets:
        prepare = _cell(matrix.results, distro, "prepare")
        build = _cell(matrix.results, distro, "build")
        test = _cell(matrix.results, distro, "test")
        ok = matrix.passed(distro)
        result = "[green]COMPATIBLE[/green]" if ok else "[red]FAILED[/red]"
        table.add_row(distro.capitalize(), prepare, build, test, result)

    console.print(table)


# ---------------------------------------------------------------------------
# analyze
# ---------------------------------------------------------------------------

@app.command()
def analyze(
    source_dir: Annotated[str, typer.Argument(help="Path to the application source directory.")],
    output: Annotated[Optional[str], typer.Option("--output", "-o", help="Write manifest JSON to this file.")] = None,
) -> None:
    """
    Run static analysis only.

    Detects portability issues and prints the issue table and portability score.
    Does not patch files or run Docker containers.
    """
    from black_flag.analyzer.runner import run_analysis, compute_score

    src = Path(source_dir).resolve()
    if not src.is_dir():
        console.print(f"[red]Error:[/red] '{source_dir}' is not a directory.")
        raise typer.Exit(code=1)

    console.print(f"\n[bold]Analyzing[/bold] [cyan]{src}[/cyan] ...\n")

    issues = run_analysis(src)
    score = compute_score(issues)

    n_err = sum(1 for i in issues if i.severity == "error")
    n_warn = sum(1 for i in issues if i.severity == "warning")

    console.print(
        f"  Found [bold]{len(issues)}[/bold] portability issue(s) "
        f"([red]{n_err} error(s)[/red], [yellow]{n_warn} warning(s)[/yellow])"
    )
    console.print(f"  Portability score: {_score_indicator(score)}\n")

    _print_issue_table(issues, score)

    if output:
        from black_flag.core.manifest import build_manifest, write_manifest
        from black_flag.core.types import TargetResult
        from black_flag.core.cache import compute_source_hash

        manifest = build_manifest(
            name=src.name,
            version="0.0.0",
            description="Static analysis only — no adaptations applied.",
            issues_before=issues,
            issues_after=issues,
            applied_diffs=[],
            target_results={
                t: TargetResult(status="UNVERIFIED") for t in ["ubuntu", "fedora", "arch"]
            },
            ai_summary="",
            ai_provider="none",
            cache_key=compute_source_hash(src),
            verified=False,
        )
        out_path = Path(output)
        write_manifest(manifest, out_path)
        console.print(f"\n💾 Manifest written to [cyan]{out_path}[/cyan]")

    console.print()
    raise typer.Exit(code=0)


# ---------------------------------------------------------------------------
# adapt
# ---------------------------------------------------------------------------

@app.command()
def adapt(
    source_dir: Annotated[str, typer.Argument(help="Path to the application source directory.")],
    max_iterations: Annotated[int, typer.Option("--max-iterations", "-n", help="Maximum repair-loop iterations.")] = 3,
    target: Annotated[str, typer.Option("--target", "-t", help="Comma-separated list of target distros.")] = "ubuntu,fedora,arch",
    dry_run: Annotated[bool, typer.Option("--dry-run", help="Analyze and plan only; do not run Docker.")] = False,
    no_ai: Annotated[bool, typer.Option("--no-ai", help="Force the deterministic provider; skip LLM calls.")] = False,
    output_dir: Annotated[Optional[str], typer.Option("--output-dir", "-O", help="Directory for the .bfpack output.")] = None,
) -> None:
    """
    Run the full portability adaptation loop.

    Analyzes the source, plans adaptations via AI (or deterministic rules),
    applies primitives, builds and tests in Docker across all target distros,
    repairs failures, and produces a .bfpack package.

    Use --dry-run to analyze + plan + diff without running Docker.
    Use --no-ai to force deterministic mode (no LLM calls).
    """
    from black_flag.analyzer.runner import run_analysis, compute_score
    from black_flag.adaptation_engine.planner import validate_plan
    from black_flag.adaptation_engine.patcher import apply_plan
    from black_flag.adaptation_engine.rollback import create_working_tree, reset_working_tree, reapply_diffs
    from black_flag.ai.factory import get_provider
    from black_flag.build.container import is_docker_available
    from black_flag.build.script_gen import generate_scripts
    from black_flag.build.matrix import run_matrix, matrix_to_target_results
    from black_flag.core.cache import compute_source_hash, cache_get, cache_set
    from black_flag.core.manifest import build_manifest, write_manifest
    from black_flag.core.types import TargetResult, TargetStatus
    from black_flag.packager.packer import create_bfpack
    from black_flag.primitives.catalog import list_primitive_ids

    src = Path(source_dir).resolve()
    if not src.is_dir():
        console.print(f"[red]Error:[/red] '{source_dir}' is not a directory.")
        raise typer.Exit(code=1)

    targets = [t.strip() for t in target.split(",") if t.strip()]
    out_dir = Path(output_dir) if output_dir else Path.cwd()

    # --- Check Docker ---
    docker_ok = is_docker_available()
    if not docker_ok and not dry_run:
        console.print(
            "[yellow]⚠️  Docker is not available.[/yellow] Running in --dry-run mode.\n"
        )
        dry_run = True

    provider = get_provider(no_ai=no_ai)
    catalog = list_primitive_ids()

    console.print(
        Panel(
            f"Source:      [cyan]{src}[/cyan]\n"
            f"Targets:     [cyan]{', '.join(targets)}[/cyan]\n"
            f"Mode:        {'[yellow]dry-run[/yellow]' if dry_run else '[green]full[/green]'}\n"
            f"AI provider: [cyan]{provider.name}[/cyan]\n"
            f"Max iters:   {max_iterations}",
            title="[bold]⚑  black-flag adapt[/bold]",
            border_style="blue",
        )
    )
    console.print()

    # -----------------------------------------------------------------------
    # Step 1: Static analysis
    # -----------------------------------------------------------------------
    console.print("🔍 [bold]Analyzing source...[/bold]")
    issues_before = run_analysis(src)
    score_before = compute_score(issues_before)
    n_err = sum(1 for i in issues_before if i.severity == "error")
    n_warn = sum(1 for i in issues_before if i.severity == "warning")
    console.print(
        f"  Found [bold]{len(issues_before)}[/bold] issue(s) "
        f"([red]{n_err} error[/red], [yellow]{n_warn} warning[/yellow])  "
        f"Score: {_score_indicator(score_before)}\n"
    )
    _print_issue_table(issues_before, score_before)
    console.print()

    if not issues_before:
        console.print("[green]✅ No portability issues detected. Application is already portable.[/green]")
        _finish_no_issues(src, targets, out_dir, provider)
        raise typer.Exit(code=0)

    # -----------------------------------------------------------------------
    # Step 2: Create working tree copy
    # -----------------------------------------------------------------------
    tmp_parent = Path(tempfile.mkdtemp(prefix="blackflag-"))
    try:
        dst = tmp_parent / src.name
        shutil.copytree(str(src), str(dst))
        working_tree = dst

        # -----------------------------------------------------------------------
        # Step 3: AI planning (Mode A)
        # -----------------------------------------------------------------------
        console.print(f"🤖 [bold]Planning adaptations[/bold] [dim]({provider.name})[/dim]...")
        raw_plan = provider.plan_adaptations(issues_before, catalog, targets)
        plan, plan_warnings = validate_plan(raw_plan)

        for w in plan_warnings:
            console.print(f"  [yellow]⚠  {w}[/yellow]")

        if plan.applications:
            table = Table(box=box.SIMPLE, show_header=False)
            for app_item in plan.applications:
                table.add_row(
                    f"  → [cyan]{app_item.primitive_id}[/cyan]",
                    f"[dim]{app_item.rationale[:70]}[/dim]",
                )
            console.print(table)
        else:
            console.print("  [yellow]No applicable primitives found by planner.[/yellow]")
        console.print()

        # -----------------------------------------------------------------------
        # Step 4: Apply adaptations
        # -----------------------------------------------------------------------
        if plan.applications:
            console.print("🔧 [bold]Applying adaptations...[/bold]")
            applied_diffs, patch_warnings = apply_plan(
                plan_applications=plan.applications,
                working_tree=working_tree,
                start_sequence=1,
                iteration=0,
            )
            for w in patch_warnings:
                console.print(f"  [yellow]⚠  {w}[/yellow]")
            for d in applied_diffs:
                console.print(f"  [green]✅[/green] {d.sequence:03d}-{d.primitive_id}.patch  ({d.target_file})")
            console.print()
        else:
            applied_diffs = []

        # -----------------------------------------------------------------------
        # Dry-run exits here
        # -----------------------------------------------------------------------
        if dry_run:
            issues_after = run_analysis(working_tree)
            score_after = compute_score(issues_after)
            console.print(
                Panel(
                    f"[yellow]Dry-run complete.[/yellow] {len(applied_diffs)} adaptation(s) applied.\n"
                    f"Score before: {_score_indicator(score_before)}  →  Score after: {_score_indicator(score_after)}\n"
                    f"No Docker matrix run. Diffs written to [cyan]{working_tree / 'diffs'}[/cyan]",
                    border_style="yellow",
                )
            )
            raise typer.Exit(code=0)

        # -----------------------------------------------------------------------
        # Step 5: Build/test matrix loop
        # -----------------------------------------------------------------------
        best_diffs: list = list(applied_diffs)
        best_matrix = None
        unsupported: set[str] = set()

        for iteration in range(max_iterations):
            console.print(
                f"🐳 [bold]Running build matrix[/bold] "
                f"[dim](iteration {iteration + 1}/{max_iterations})[/dim]..."
            )

            # Regenerate stage scripts in case files changed
            generate_scripts(working_tree)

            live_results: list = []

            def _on_result(r) -> None:
                live_results.append(r)
                icon = "✅" if r.exit_code == 0 else ("—" if r.exit_code == -1 else "❌")
                color = "green" if r.exit_code == 0 else ("dim" if r.exit_code == -1 else "red")
                console.print(
                    f"  [{color}]{icon}[/{color}]  {r.distro:8s}  {r.stage:8s}  "
                    f"[dim]{r.elapsed_s:.1f}s[/dim]"
                )

            matrix = run_matrix(
                working_tree=working_tree,
                targets=[t for t in targets if t not in unsupported],
                iteration=iteration,
                progress_cb=_on_result,
            )
            console.print()
            _print_matrix_table(matrix, [t for t in targets if t not in unsupported])
            console.print()

            if matrix.all_passed():
                best_matrix = matrix
                best_diffs = applied_diffs
                console.print("[green]✅ All targets compatible![/green]\n")
                break

            # Save best state before repair
            if best_matrix is None or sum(1 for r in matrix.results if r.exit_code == 0) >= sum(1 for r in best_matrix.results if r.exit_code == 0):
                best_matrix = matrix
                best_diffs = list(applied_diffs)

            # -----------------------------------------------------------------------
            # Repair: diagnose each failure
            # -----------------------------------------------------------------------
            if iteration < max_iterations - 1:
                console.print(f"🩺 [bold]Diagnosing failures...[/bold] [dim](AI: {provider.name})[/dim]")
                repaired_any = False

                for failed_r in matrix.failed_results():
                    if failed_r.distro in unsupported or failed_r.exit_code == -1:
                        continue

                    repair = provider.diagnose_failure(failed_r, applied_diffs, catalog)
                    if repair.action == "give_up":
                        console.print(
                            f"  [red]Give up[/red]  {failed_r.distro}/{failed_r.stage}: "
                            f"{repair.rationale[:80]}"
                        )
                        unsupported.add(failed_r.distro)
                        continue

                    console.print(
                        f"  [cyan]→ {repair.primitive_id}[/cyan]  "
                        f"{failed_r.distro}/{failed_r.stage}: {repair.rationale[:60]}"
                    )

                    # Roll back to best and re-apply + repair
                    reapplied, rw = reapply_diffs(best_diffs, working_tree, src)
                    from black_flag.primitives.catalog import get_primitive
                    from black_flag.adaptation_engine.patcher import apply_primitive
                    from black_flag.primitives.base import PrimitiveNotApplicable
                    from black_flag.adaptation_engine.patcher import _find_matching_file

                    prim = get_primitive(repair.primitive_id)
                    target_file = repair.params.get("file") or _find_matching_file(prim, working_tree)
                    if target_file:
                        try:
                            new_diff = apply_primitive(
                                primitive=prim,
                                params=repair.params,
                                target_file_rel=target_file,
                                working_tree=working_tree,
                                sequence=len(applied_diffs) + 1,
                                rationale=repair.rationale,
                                affected_targets=[failed_r.distro],
                                iteration=iteration + 1,
                            )
                            applied_diffs.append(new_diff)
                            console.print(
                                f"    [green]✅[/green] {new_diff.sequence:03d}-{new_diff.primitive_id}.patch"
                            )
                            repaired_any = True
                        except (PrimitiveNotApplicable, FileNotFoundError) as e:
                            console.print(f"    [yellow]⚠[/yellow] {e}")

                console.print()
                if not repaired_any:
                    console.print("[yellow]No repairs applied. Stopping repair loop.[/yellow]\n")
                    break

        # -----------------------------------------------------------------------
        # Step 6: Compute final state and package
        # -----------------------------------------------------------------------
        issues_after = run_analysis(working_tree)
        score_after = compute_score(issues_after)

        console.print(
            f"📊 [bold]Portability score:[/bold] "
            f"{_score_indicator(score_before)} → {_score_indicator(score_after)}\n"
        )

        # Determine final target results
        if best_matrix:
            target_results = matrix_to_target_results(best_matrix, targets)
        else:
            target_results = {
                t: TargetResult(status="UNVERIFIED") for t in targets
            }
        for t in unsupported:
            target_results[t] = TargetResult(status="UNSUPPORTED")

        cache_key = compute_source_hash(src)
        ai_summary = provider.summarize(
            issues=issues_after,
            applied_diffs=applied_diffs,
            passed_targets=[t for t in targets if target_results.get(t) and target_results[t].status == "COMPATIBLE"],
            failed_targets=[t for t in targets if target_results.get(t) and target_results[t].status in ("FAILED", "UNSUPPORTED")],
        )

        manifest = build_manifest(
            name=src.name,
            version="0.1.0",
            description=f"Portable package produced by black-flag adapt",
            issues_before=issues_before,
            issues_after=issues_after,
            applied_diffs=applied_diffs,
            target_results=target_results,
            ai_summary=ai_summary,
            ai_provider=provider.name,
            cache_key=cache_key,
            verified=not dry_run,
        )

        # Generate scripts one final time before packaging
        generate_scripts(working_tree)

        archive = create_bfpack(
            working_tree=working_tree,
            manifest=manifest,
            output_dir=out_dir,
        )

        # Update cache
        passed = [t for t, r in target_results.items() if r.status == "COMPATIBLE"]
        cache_set(cache_key, {
            "name": src.name,
            "score": score_after,
            "passed_targets": passed,
            "archive": str(archive),
        })

        # Final summary
        _print_final_summary(manifest, archive, target_results, targets, score_before, score_after)

    finally:
        shutil.rmtree(tmp_parent, ignore_errors=True)

    raise typer.Exit(code=0)


def _finish_no_issues(src: Path, targets: list[str], out_dir: Path, provider) -> None:
    """Package source as-is when no portability issues are found."""
    from black_flag.adaptation_engine.rollback import create_working_tree
    from black_flag.build.script_gen import generate_scripts
    from black_flag.core.manifest import build_manifest
    from black_flag.core.cache import compute_source_hash
    from black_flag.core.types import TargetResult
    from black_flag.packager.packer import create_bfpack
    import shutil, tempfile

    tmp = Path(tempfile.mkdtemp(prefix="blackflag-"))
    try:
        dst = tmp / src.name
        shutil.copytree(str(src), str(dst))
        generate_scripts(dst)
        manifest = build_manifest(
            name=src.name,
            version="0.1.0",
            description="No adaptations needed — application is already portable.",
            issues_before=[],
            issues_after=[],
            applied_diffs=[],
            target_results={t: TargetResult(status="COMPATIBLE", prepare=None, build=None, test=None) for t in targets},
            ai_summary="No portability issues detected.",
            ai_provider=provider.name,
            cache_key=compute_source_hash(src),
            verified=False,
        )
        archive = create_bfpack(working_tree=dst, manifest=manifest, output_dir=out_dir)
        console.print(f"\n📦 Package created: [bold cyan]{archive}[/bold cyan]")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _print_final_summary(manifest, archive: Path, target_results: dict, targets: list[str], score_before: float, score_after: float) -> None:
    """Print the final portability report."""
    status_icons = {
        "COMPATIBLE": "[green]✅ COMPATIBLE[/green]",
        "FAILED": "[red]❌ FAILED[/red]",
        "UNSUPPORTED": "[red]⛔ UNSUPPORTED[/red]",
        "UNVERIFIED": "[yellow]⚠  UNVERIFIED[/yellow]",
    }

    lines = [
        f"Application: [bold]{manifest.name}[/bold]  v{manifest.version}",
        f"Architecture: x86-64",
        "",
    ]
    for t in targets:
        tr = target_results.get(t)
        status = tr.status if tr else "UNVERIFIED"
        lines.append(f"  {t.capitalize():8s}  {status_icons.get(status, status)}")

    lines += [
        "",
        f"Score:  {_score_indicator(score_before)} → {_score_indicator(score_after)}",
        f"Diffs:  {len(manifest.applied_adaptations)} adaptation(s) applied",
        "",
        f"📦 [bold]{archive.name}[/bold]  ({archive.stat().st_size // 1024} KB)",
    ]

    if manifest.ai_summary:
        lines += ["", f"[dim]{manifest.ai_summary}[/dim]"]

    console.print(
        Panel(
            "\n".join(lines),
            title="[bold]⚑  BLACK FLAG PORTABILITY REPORT[/bold]",
            border_style="green" if all(
                target_results.get(t) and target_results[t].status == "COMPATIBLE"
                for t in targets
            ) else "yellow",
        )
    )


# ---------------------------------------------------------------------------
# install
# ---------------------------------------------------------------------------

@app.command()
def install(
    bfpack_file: Annotated[str, typer.Argument(help="Path to the .bfpack file to install.")],
    force: Annotated[bool, typer.Option("--force", "-f", help="Reinstall even if already installed.")] = False,
) -> None:
    """
    Install a .bfpack package.

    Extracts the package, detects the running distro, runs prepare + build + test
    scripts, and symlinks the entry point into ~/.local/bin/.
    """
    from black_flag.installer.installer import install_bfpack, InstallError

    bfpack = Path(bfpack_file).resolve()

    console.print(f"\n📦 Installing [cyan]{bfpack.name}[/cyan]...\n")

    try:
        result = install_bfpack(bfpack, force=force)
        console.print(
            Panel(
                f"[green]✅ {result['message']}[/green]\n\n"
                f"  Name:        {result['name']}\n"
                f"  Version:     {result['version']}\n"
                f"  Distro:      {result['distro']}\n"
                f"  Install dir: {result['install_dir']}\n"
                f"  Entry point: {result['entry_point']}\n\n"
                f"  Run: [bold]{result['name']}[/bold]",
                title="Installation Complete",
                border_style="green",
            )
        )
    except InstallError as e:
        console.print(
            Panel(
                f"[red]❌ Installation failed:[/red]\n\n{e}",
                title="Installation Failed",
                border_style="red",
            )
        )
        raise typer.Exit(code=1)

    raise typer.Exit(code=0)


# ---------------------------------------------------------------------------
# status
# ---------------------------------------------------------------------------

@app.command()
def status(
    name: Annotated[Optional[str], typer.Argument(help="Package name to inspect. Lists all if omitted.")] = None,
) -> None:
    """
    Show installed Black Flag packages and their compatibility status.
    """
    from black_flag.installer.installer import list_installed

    packages = list_installed()

    if not packages:
        console.print("\n[dim]No Black Flag packages installed.[/dim]")
        console.print(f"Install one with: [cyan]black-flag install <file.bfpack>[/cyan]\n")
        raise typer.Exit(code=0)

    if name:
        packages = [p for p in packages if p.get("name") == name]
        if not packages:
            console.print(f"\n[yellow]Package '{name}' is not installed.[/yellow]\n")
            raise typer.Exit(code=1)

    table = Table(title="Installed Black Flag Packages", box=box.SIMPLE_HEAVY)
    table.add_column("Name", style="bold")
    table.add_column("Version")
    table.add_column("Score")
    table.add_column("Ubuntu", justify="center")
    table.add_column("Fedora", justify="center")
    table.add_column("Arch", justify="center")
    table.add_column("Installed At")

    status_icon = {
        "COMPATIBLE": "[green]✅[/green]",
        "FAILED": "[red]❌[/red]",
        "UNSUPPORTED": "[red]⛔[/red]",
        "UNVERIFIED": "[yellow]?[/yellow]",
    }

    for pkg in packages:
        if "error" in pkg:
            table.add_row(pkg["name"], "—", "—", "—", "—", "—", "parse error")
            continue
        targets = pkg.get("targets", {})
        table.add_row(
            pkg.get("name", "?"),
            pkg.get("version", "?"),
            _score_indicator(pkg.get("score", 0.0)),
            status_icon.get(targets.get("ubuntu", ""), "—"),
            status_icon.get(targets.get("fedora", ""), "—"),
            status_icon.get(targets.get("arch", ""), "—"),
            pkg.get("created_at", "")[:10],
        )

    console.print()
    console.print(table)
    console.print()
    raise typer.Exit(code=0)


# ---------------------------------------------------------------------------
# info  (always available)
# ---------------------------------------------------------------------------

@app.command()
def info() -> None:
    """
    Display the Black Flag environment: Python version, Docker status,
    detected host distro, and all supported target adapters.
    """
    import subprocess

    from black_flag.runtime.detector import detect_adapter, get_all_adapters, detect_distro_id

    console.print()
    console.print("[bold]Black Flag[/bold] — environment info\n")

    # Python
    console.print(f"  Python:      [cyan]{sys.version.split()[0]}[/cyan]")

    # Docker
    try:
        result = subprocess.run(
            ["docker", "info", "--format", "{{.ServerVersion}}"],
            capture_output=True, text=True, timeout=5
        )
        if result.returncode == 0:
            console.print(f"  Docker:      [green]running[/green] (v{result.stdout.strip()})")
        else:
            console.print("  Docker:      [red]not available[/red] — will use --dry-run mode")
    except Exception:
        console.print("  Docker:      [red]not available[/red] — will use --dry-run mode")

    # AI provider
    import os
    ai_key = "WATSONX_API_KEY"
    if os.environ.get(ai_key):
        model = os.environ.get("WATSONX_MODEL_ID", "default")
        console.print(f"  AI:          [green]watsonx[/green] (model={model})")
    else:
        console.print("  AI:          [yellow]deterministic[/yellow] (set WATSONX_API_KEY to enable watsonx.ai)")

    # Host distro
    distro_id = detect_distro_id()
    active = detect_adapter()
    if active:
        console.print(f"  Host distro: [green]{active.display_name}[/green] (ID={distro_id!r})")
    else:
        console.print(f"  Host distro: [yellow]unsupported[/yellow] (ID={distro_id!r}) — adapt and install will use --dry-run")

    # Supported targets table
    console.print()
    table = Table(title="Supported Target Adapters", box=box.SIMPLE_HEAVY)
    table.add_column("Distro", style="bold")
    table.add_column("ID")
    table.add_column("Package Manager")
    table.add_column("Docker Image")

    for adapter in get_all_adapters():
        table.add_row(
            adapter.display_name,
            adapter.distro_id,
            adapter.package_manager(),
            adapter.docker_image,
        )

    console.print(table)
    console.print()
