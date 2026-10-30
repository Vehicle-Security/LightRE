#!/usr/bin/env bash

set -Eeuo pipefail

SCRIPT_PATH="$(readlink -f -- "${BASH_SOURCE[0]}")"
SCRIPT_DIR="$(cd -- "$(dirname -- "$SCRIPT_PATH")" && pwd)"
TOOLS_DIR="${TOOLS_DIR:-$SCRIPT_DIR/tools}"
RIZIN_HOME="${RIZIN_HOME:-$TOOLS_DIR/rizin}"
GHIDRA_HOME="${GHIDRA_HOME:-$TOOLS_DIR/ghidra}"
GHIDRA_URL="${GHIDRA_URL:-}"

die() {
  printf 'error: %s\n' "$*" >&2
  exit 1
}

log() {
  printf '[install_deps] %s\n' "$*"
}

as_root() {
  if [[ "${EUID}" -eq 0 ]]; then
    "$@"
  elif command -v sudo >/dev/null 2>&1; then
    sudo "$@"
  else
    die "需要 root 权限安装系统依赖，但未找到 sudo。请以 root 执行，或手动安装依赖。"
  fi
}

install_system_packages() {
  command -v apt-get >/dev/null 2>&1 || die "此脚本仅支持 Ubuntu 24.04。"

  log "安装 Ubuntu 24.04 依赖"
  as_root apt-get update
  as_root apt-get install -y \
    ca-certificates curl git python3 unzip \
    openjdk-21-jdk \
    build-essential meson ninja-build pkg-config \
    libcapstone-dev liblz4-dev libzip-dev libxxhash-dev \
    libmagic-dev libssl-dev libzstd-dev libtree-sitter-dev
}

build_rizin_from_source() {
  local source_dir="$TOOLS_DIR/src/rizin"
  local build_dir="$source_dir/build"

  log "系统包中没有 Rizin，改为从源码构建"
  mkdir -p "$TOOLS_DIR/src"

  if [[ ! -d "$source_dir/.git" ]]; then
    git clone --depth 1 https://github.com/rizinorg/rizin.git "$source_dir"
  fi

  if [[ -f "$build_dir/meson-private/coredata.dat" ]]; then
    meson setup --reconfigure "$build_dir" "$source_dir" --prefix="$RIZIN_HOME" --buildtype=release
  else
    meson setup "$build_dir" "$source_dir" --prefix="$RIZIN_HOME" --buildtype=release
  fi
  meson compile -C "$build_dir"
  meson install -C "$build_dir"
}

install_rizin() {
  if command -v rizin >/dev/null 2>&1; then
    log "Rizin 已存在：$(command -v rizin)"
    return
  fi
  if [[ -x "$RIZIN_HOME/bin/rizin" ]]; then
    log "Rizin 已存在：$RIZIN_HOME/bin/rizin"
    return
  fi

  build_rizin_from_source
}

resolve_ghidra_url() {
  if [[ -n "$GHIDRA_URL" ]]; then
    printf '%s\n' "$GHIDRA_URL"
    return
  fi

  log "查询 Ghidra 最新公开发行版" >&2
  curl -fsSL https://api.github.com/repos/NationalSecurityAgency/ghidra/releases/latest \
    | python3 -c '
import json, sys
data = json.load(sys.stdin)
assets = data.get("assets", [])
for asset in assets:
    name = asset.get("name", "")
    if "_PUBLIC_" in name and name.endswith(".zip") and "-src" not in name:
        print(asset["browser_download_url"])
        break
else:
    raise SystemExit("未找到 Ghidra PUBLIC zip 发行包")
'
}

install_ghidra() {
  local url archive temp_dir extracted_dir

  if [[ -x "$GHIDRA_HOME/support/analyzeHeadless" ]]; then
    log "Ghidra Headless 已存在：$GHIDRA_HOME"
    return
  fi

  url="$(resolve_ghidra_url)"
  archive="$TOOLS_DIR/cache/$(basename "${url%%\?*}")"
  temp_dir="$TOOLS_DIR/.ghidra-extract.$$"

  mkdir -p "$TOOLS_DIR/cache"
  if [[ ! -f "$archive" ]]; then
    log "下载 Ghidra：$url"
    curl -fL --retry 3 --output "$archive" "$url"
  else
    log "使用已有归档：$archive"
  fi

  mkdir -p "$temp_dir"
  trap 'rm -rf "$temp_dir"' RETURN
  unzip -q "$archive" -d "$temp_dir"
  extracted_dir="$(find "$temp_dir" -mindepth 1 -maxdepth 1 -type d -print -quit)"
  [[ -n "$extracted_dir" ]] || die "无法从 Ghidra 归档中找到安装目录"

  if [[ -e "$GHIDRA_HOME" ]]; then
    die "$GHIDRA_HOME 已存在但不是可用的 Ghidra 安装；请设置 GHIDRA_HOME 或移走该目录后重试。"
  fi
  mv "$extracted_dir" "$GHIDRA_HOME"
  rm -rf "$temp_dir"
  trap - RETURN
  [[ -x "$GHIDRA_HOME/support/analyzeHeadless" ]] || die "Ghidra 安装完成但找不到 support/analyzeHeadless"
}

main() {
  cd -- "$SCRIPT_DIR"
  mkdir -p "$TOOLS_DIR"
  install_system_packages
  install_rizin
  install_ghidra

  log "安装完成"
  printf '\n请在 shell 中执行：\n'
  printf '  source %q\n' "$SCRIPT_DIR/set_env.sh"
  printf '\n检查命令：\n'
  printf '  rizin -v\n'
  printf '  %q\n' "$GHIDRA_HOME/support/analyzeHeadless"
}

main "$@"
