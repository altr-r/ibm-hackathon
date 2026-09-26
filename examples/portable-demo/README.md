# portable-demo

A small Python application used as the **Black Flag demo target**.

## Purpose

`portable-demo` intentionally contains portability flaws that cause it to
fail on Fedora and Arch Linux. Running `black-flag adapt` on this directory
demonstrates the full adaptation loop.

## Deliberate Flaws

| Flaw | File | Line | Category | Primitive |
|------|------|------|----------|-----------|
| `#!/bin/bash` shebang (assumes /bin/bash location) | `setup.sh` | 1 | shell-ism | PRIM-004 `shell_compat` |
| `apt-get install libssl-dev` (Debian-only) | `setup.sh` | 13 | package-manager | PRIM-001 `pkg_manager_call` |
| `open("/etc/debian_version")` (Debian-only path) | `app.py` | 24 | hardcoded-path | PRIM-002 `distro_path_check` |
| `os.environ["DEBIAN_FRONTEND"]` (KeyError on non-Debian) | `app.py` | 33 | env-assumption | PRIM-003 `env_var_portability` |

## Expected behaviour before Black Flag

| Distro | Result | Reason |
|--------|--------|--------|
| Ubuntu 22.04 | ✅ PASS | `apt-get` and `/etc/debian_version` are present |
| Fedora 41 | ❌ FAIL | `apt-get` not found (prepare stage) |
| Arch Linux | ❌ FAIL | `apt-get` not found (prepare stage) |

## Expected behaviour after Black Flag

All three distros: ✅ PREPARE ✅ BUILD ✅ TEST

## Quick start

```bash
# Analyze only (no changes made)
black-flag analyze examples/portable-demo/

# Full adaptation loop
black-flag adapt examples/portable-demo/
```

## Extending the demo

The architecture is designed to support more realistic demo applications.
To add your own:

1. Create a new directory under `examples/`
2. Add `app.py` (or any source files) and `setup.sh`
3. Add `test.sh` with a command that exits 0 on success
4. Run `black-flag analyze <your-dir>` to see detected issues
