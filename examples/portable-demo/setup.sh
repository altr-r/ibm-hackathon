#!/bin/bash
# portable-demo/setup.sh — Install script for portable-demo.
#
# FLAW 1 (PRIM-004): #!/bin/bash shebang assumes /bin/bash location.
#   On some minimal Arch containers bash is at /usr/bin/bash.
#   Also, [[ below is a bash-ism that fails under /bin/sh.
#
# FLAW 2 (PRIM-001): apt-get is Debian/Ubuntu-specific.
#   This script will fail immediately on Fedora (dnf) and Arch (pacman).
#   libssl-dev is also the Debian name (Fedora: openssl-devel, Arch: openssl).
#
# DO NOT FIX THESE FLAWS MANUALLY — Black Flag will fix them automatically.

apt-get install -y libssl-dev python3-cryptography

if [[ -z "$VIRTUAL_ENV" ]]; then
    echo "Not running in a Python virtualenv"
fi
