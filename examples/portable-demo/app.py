#!/usr/bin/env python3
"""
portable-demo/app.py — Demo application for Black Flag.

This application contains FOUR deliberate portability flaws that map
exactly to the four adaptation primitives exercised in the demo:

  Flaw 3 (PRIM-002): Hardcoded /etc/debian_version path check
  Flaw 4 (PRIM-003): os.environ["DEBIAN_FRONTEND"] raises KeyError on non-Debian

The remaining flaws are in setup.sh (Flaws 1 and 2).

DO NOT FIX THESE FLAWS MANUALLY — Black Flag will fix them automatically.
"""
import os
import ssl


def main() -> None:
    print("=== portable-demo sysinfo ===")

    # Flaw 3 (PRIM-002): /etc/debian_version does not exist on Fedora or Arch.
    # This causes a RuntimeError on all non-Debian systems.
    if os.path.exists("/etc/debian_version"):
        with open("/etc/debian_version") as f:
            print(f"Debian version: {f.read().strip()}")
    else:
        raise RuntimeError(
            "This application requires a Debian-based system. "
            "Run black-flag adapt to make it portable."
        )

    # Flaw 4 (PRIM-003): DEBIAN_FRONTEND is a Debian CI convention.
    # It is not set on Fedora or Arch — this line raises KeyError.
    frontend = os.environ["DEBIAN_FRONTEND"]
    print(f"Package frontend: {frontend}")

    # SSL check (works on all distros once libssl-dev / openssl is installed)
    ctx = ssl.create_default_context()
    print(f"SSL: {ssl.OPENSSL_VERSION}")
    print("PASS: sysinfo check completed")


if __name__ == "__main__":
    main()
