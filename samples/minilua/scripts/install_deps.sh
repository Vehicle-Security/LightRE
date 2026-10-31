#!/usr/bin/env bash
set -euo pipefail
# Ubuntu 24.04 / Debian with LLVM 14 packages. Run as root (or via sudo).
apt-get update
apt-get install -y clang-14 clang-format-14 llvm-14-dev cmake ninja-build g++ git curl patch \
  python3 gcc-aarch64-linux-gnu binutils-aarch64-linux-gnu
if [[ "$(uname -m)" != aarch64 && "$(uname -m)" != arm64 ]]; then
  apt-get install -y qemu-user libc6-dev-arm64-cross
fi
