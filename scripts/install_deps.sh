#!/usr/bin/env bash

set -Eeuo pipefail

SCRIPT_DIR="$(dirname -- "$(readlink -f -- "${BASH_SOURCE[0]}")")"
PROJECT_DIR="$(dirname -- "$SCRIPT_DIR")"
TOOLS_DIR="$PROJECT_DIR/tools"
RIZIN_HOME="$TOOLS_DIR/rizin"
GHIDRA_HOME="$TOOLS_DIR/ghidra"
GHIDRA_URL="${GHIDRA_URL:-}"
INSTALL_GHIDRA=true
INSTALL_RIZIN=false
INSTALL_RUNTIME=true
INSTALL_ANDROID=false

die() {
  printf 'error: %s\n' "$*" >&2
  exit 1
}

log() {
  printf '[install_deps] %s\n' "$*"
}

has_rizin() {
  command -v rizin >/dev/null 2>&1 || {
    [[ -x "$RIZIN_HOME/bin/rizin" ]] &&
      [[ -d "$("$RIZIN_HOME/bin/rizin" -H RZ_PREFIX)" ]]
  }
}

ghidra_platform() {
  case "$(uname -m)" in
    x86_64) printf 'linux_x86_64' ;;
    aarch64) printf 'linux_arm_64' ;;
    *) die "不支持的 Linux 架构: $(uname -m)" ;;
  esac
}

has_ghidra() {
  [[ -x "$GHIDRA_HOME/support/analyzeHeadless" &&
     -x "$GHIDRA_HOME/Ghidra/Features/Decompiler/os/$(ghidra_platform)/decompile" &&
     -x "$GHIDRA_HOME/Ghidra/Features/Decompiler/os/$(ghidra_platform)/sleigh" ]]
}

has_java() {
  local major compiler_major required=17

  if "$INSTALL_GHIDRA"; then
    required=25
    if [[ -f "$GHIDRA_HOME/Ghidra/application.properties" ]]; then
      required="$(sed -n 's/^application.java.min=//p' "$GHIDRA_HOME/Ghidra/application.properties")"
    fi
  fi

  command -v java >/dev/null 2>&1 || return 1
  major="$(java -version 2>&1 | sed -n 's/.*version "\([0-9][0-9]*\).*/\1/p' | head -n 1)"
  [[ "$major" =~ ^[0-9]+$ ]] && (( major >= required )) || return 1
  if "$INSTALL_RUNTIME"; then
    command -v javac >/dev/null 2>&1 || return 1
    compiler_major="$(javac -version 2>&1 | sed -n 's/^javac \([0-9][0-9]*\).*/\1/p' | head -n 1)"
    [[ "$compiler_major" =~ ^[0-9]+$ ]] && (( compiler_major >= 17 ))
  fi
}

has_build_dependencies() {
  local command_name

  for command_name in curl git python3 unzip make gcc g++ bison flex clang-format; do
    command -v "$command_name" >/dev/null 2>&1 || return 1
  done
  python3 -c 'import ensurepip, markdown_it' >/dev/null 2>&1 || return 1
  if "$INSTALL_RUNTIME"; then
    for command_name in javac mvn clang ld.lld; do
      command -v "$command_name" >/dev/null 2>&1 || return 1
    done
  fi
  if "$INSTALL_RIZIN"; then
    for command_name in meson ninja pkg-config; do
      command -v "$command_name" >/dev/null 2>&1 || return 1
    done
  fi
  if "$INSTALL_ANDROID"; then
    for command_name in clang-14 llvm-config-14 opt-14 llc-14 cmake ninja patch readelf; do
      command -v "$command_name" >/dev/null 2>&1 || return 1
    done
  fi
  { ! "$INSTALL_GHIDRA" && ! "$INSTALL_RUNTIME"; } || has_java
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
  local java_packages=() rizin_packages=() runtime_packages=()
  if "$INSTALL_GHIDRA"; then
    java_packages=(openjdk-25-jdk)
  elif "$INSTALL_RUNTIME"; then
    java_packages=(default-jdk)
  fi
  if "$INSTALL_RUNTIME"; then
    runtime_packages=(maven clang lld)
  fi
  if "$INSTALL_ANDROID"; then
    runtime_packages+=(clang-14 llvm-14-dev cmake ninja-build patch binutils)
  fi
  if "$INSTALL_RIZIN"; then
    rizin_packages=(meson ninja-build pkg-config libcapstone-dev liblz4-dev libzip-dev
      libxxhash-dev libmagic-dev libssl-dev libzstd-dev libtree-sitter-dev)
  fi
  command -v apt-get >/dev/null 2>&1 || die "此脚本仅支持 Ubuntu 24.04。"

  log "安装 Ubuntu 24.04 依赖"
  as_root apt-get update
  as_root apt-get install -y \
    ca-certificates curl git python3 python3-venv python3-markdown-it unzip \
    "${java_packages[@]}" \
    build-essential bison flex clang-format zlib1g-dev \
    "${runtime_packages[@]}" "${rizin_packages[@]}"
}

build_rizin_from_source() {
  local source_dir="$TOOLS_DIR/src/rizin"
  local build_dir="$source_dir/build-lightre"

  log "系统包中没有 Rizin，改为从源码构建"
  mkdir -p "$TOOLS_DIR/src"

  if [[ ! -d "$source_dir/.git" ]]; then
    git clone --depth 1 https://github.com/rizinorg/rizin.git "$source_dir"
  fi

  if [[ -f "$build_dir/meson-private/coredata.dat" ]]; then
    meson setup --reconfigure "$build_dir" "$source_dir" --prefix="$RIZIN_HOME" --buildtype=release -Dportable=true
  else
    meson setup "$build_dir" "$source_dir" --prefix="$RIZIN_HOME" --buildtype=release -Dportable=true
  fi
  meson compile -C "$build_dir"
  meson install -C "$build_dir"
}

install_rizin() {
  if has_rizin; then
    if command -v rizin >/dev/null 2>&1; then
      log "Rizin 已存在：$(command -v rizin)"
    else
      log "Rizin 已存在：$RIZIN_HOME/bin/rizin"
    fi
    return
  fi

  build_rizin_from_source
}

has_android_sysroot() {
  local ndk="$TOOLS_DIR/android-ndk-r27d"
  [[ -f "$ndk/source.properties" &&
     -f "$ndk/toolchains/llvm/prebuilt/linux-x86_64/sysroot/usr/include/android/api-level.h" &&
     -f "$ndk/toolchains/llvm/prebuilt/linux-x86_64/sysroot/usr/lib/aarch64-linux-android/23/libc.so" ]]
}

install_android_sysroot() {
  # Use the native host Clang/LLD, including on aarch64 Linux. NDK's x86 compiler
  # is not executed; only the official Bionic headers and target libraries are used.
  has_android_sysroot && { log 'Android NDK r27d sysroot 已存在'; return; }
  local archive="$TOOLS_DIR/cache/android-ndk-r27d-linux.zip"
  mkdir -p "$TOOLS_DIR/cache"
  if [[ ! -f "$archive" ]]; then
    log '下载 Android NDK r27d（仅提取 sysroot，不运行 x86_64 编译器）'
    curl -fsSL --retry 3 -o "$archive.part" \
      https://dl.google.com/android/repository/android-ndk-r27d-linux.zip
    mv "$archive.part" "$archive"
  fi
  # Checksum published in Google's repository2-1.xml for ndk;27.3.13750724.
  printf '%s  %s\n' '22105e410cf29afcf163760cc95522b9fb981121' "$archive" | sha1sum -c -
  unzip -q -o "$archive" 'android-ndk-r27d/source.properties' \
    'android-ndk-r27d/toolchains/llvm/prebuilt/linux-x86_64/sysroot/*' -d "$TOOLS_DIR"
  has_android_sysroot || die 'Android sysroot 提取不完整'
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
    die "$GHIDRA_HOME 已存在但不是可用的 Ghidra 安装；请检查或移走该目录后重试。"
  fi
  mv "$extracted_dir" "$GHIDRA_HOME"
  rm -rf "$temp_dir"
  trap - RETURN
  [[ -x "$GHIDRA_HOME/support/analyzeHeadless" ]] || die "Ghidra 安装完成但找不到 support/analyzeHeadless"
}

prepare_ghidra_native() {
  local platform native source
  platform="$(ghidra_platform)"
  native="$GHIDRA_HOME/Ghidra/Features/Decompiler/os/$platform"
  [[ -x "$native/decompile" && -x "$native/sleigh" ]] && return
  source="$GHIDRA_HOME/Ghidra/Features/Decompiler/src/decompile/cpp"
  log "构建 Ghidra 本机反编译器：$platform"
  make -C "$source" -j"${BUILD_JOBS:-4}" ghidra_opt ARCH_TYPE= OSDIR="$platform"
  make -C "$source" -j"${BUILD_JOBS:-4}" sleigh_opt ARCH_TYPE= OSDIR="$platform"
  mkdir -p "$native"
  install -m 755 "$source/ghidra_opt" "$native/decompile"
  install -m 755 "$source/sleigh_opt" "$native/sleigh"
}

main() {
  case "$*" in
    "") ;;
    --with-rizin) INSTALL_RIZIN=true ;;
    --runtime-only) INSTALL_GHIDRA=false ;;
    --android) INSTALL_GHIDRA=false; INSTALL_ANDROID=true ;;
    --rizin-only) INSTALL_GHIDRA=false; INSTALL_RIZIN=true; INSTALL_RUNTIME=false ;;
    *) die "用法：bash scripts/install_deps.sh [--with-rizin | --runtime-only | --android | --rizin-only]" ;;
  esac
  cd -- "$PROJECT_DIR"
  mkdir -p "$TOOLS_DIR"

  if { ! "$INSTALL_RIZIN" || has_rizin; } && { ! "$INSTALL_GHIDRA" || has_ghidra; } && has_build_dependencies; then
    if "$INSTALL_ANDROID"; then install_android_sysroot; fi
    log "所需工具及其依赖都已存在，跳过安装"
    return 0
  fi

  install_system_packages
  if "$INSTALL_RIZIN"; then
    install_rizin
  fi
  if "$INSTALL_GHIDRA"; then
    install_ghidra
    prepare_ghidra_native
  fi
  if "$INSTALL_ANDROID"; then install_android_sysroot; fi

  log "安装完成"
  if "$INSTALL_GHIDRA"; then
    printf '\n请在 shell 中执行：\n'
    printf '  source %q\n' "$PROJECT_DIR/set_env.sh"
  fi
  printf '\n检查命令：\n'
  if "$INSTALL_RIZIN"; then
    printf '  %q -v\n' "$RIZIN_HOME/bin/rizin"
  fi
  if "$INSTALL_GHIDRA"; then
    printf '  %q\n' "$GHIDRA_HOME/support/analyzeHeadless"
  fi
  if "$INSTALL_RUNTIME"; then
    printf '  java -version\n  javac -version\n  mvn -version\n  clang --version\n  ld.lld --version\n'
  fi
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  main "$@"
fi
