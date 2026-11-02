PRJ_DIR="$(dirname -- "$(readlink -f -- "${BASH_SOURCE[0]}")")"

if [[ ! -x "${LIGHTRE_TOOLS_DIR:-$PRJ_DIR/tools}/ghidra/support/analyzeHeadless" ]]; then
  bash "$PRJ_DIR/scripts/install_deps.sh"
fi

export PRJ_DIR
