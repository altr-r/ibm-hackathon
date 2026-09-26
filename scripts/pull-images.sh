#!/usr/bin/env bash
# scripts/pull-images.sh — Pre-pull all three Docker base images.
#
# Run this BEFORE the demo to ensure images are available locally.
# Estimated pull size: ~80 MB (ubuntu) + ~55 MB (fedora) + ~130 MB (archlinux)
#
# Usage:
#   bash scripts/pull-images.sh

set -euo pipefail

IMAGES=(
    "ubuntu:22.04"
    "fedora:41"
    "archlinux:latest"
)

echo "Pulling Black Flag base images..."
for image in "${IMAGES[@]}"; do
    echo "  → $image"
    docker pull "$image"
    echo "    ✅ $image ready"
done

echo ""
echo "All images pulled. Verifying /etc/os-release ID values:"
for image in "${IMAGES[@]}"; do
    id=$(docker run --rm "$image" bash -c '. /etc/os-release && echo $ID')
    echo "  $image → ID=$id"
done

echo ""
echo "Done. Run 'black-flag info' to confirm the environment."
