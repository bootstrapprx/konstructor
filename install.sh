#!/usr/bin/env bash
set -euo pipefail

SOURCE_DIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
if [[ "${EUID:-$(id -u)}" -eq 0 ]]; then DEFAULT_PREFIX="/usr/local"; else DEFAULT_PREFIX="${HOME}/.local"; fi
PREFIX="$DEFAULT_PREFIX"
BIN_DIR=""
DRY_RUN=0
UNINSTALL=0

usage() {
  cat <<'USAGE'
Uso: ./install.sh [opções]

Opções:
  --prefix DIR       prefixo de instalação (padrão: /usr/local como root, ~/.local caso contrário)
  --bin-dir DIR      diretório do launcher (padrão: PREFIX/bin)
  --dry-run          exibe ações sem escrever arquivos
  --uninstall        remove a instalação correspondente ao prefixo
  -h, --help         exibe esta ajuda
USAGE
}

while (($#)); do
  case "$1" in
    --prefix) [[ $# -ge 2 ]] || { echo "--prefix exige um diretório" >&2; exit 2; }; PREFIX="$2"; shift 2;;
    --bin-dir) [[ $# -ge 2 ]] || { echo "--bin-dir exige um diretório" >&2; exit 2; }; BIN_DIR="$2"; shift 2;;
    --dry-run) DRY_RUN=1; shift;;
    --uninstall) UNINSTALL=1; shift;;
    -h|--help) usage; exit 0;;
    *) echo "opção desconhecida: $1" >&2; usage >&2; exit 2;;
  esac
done

PREFIX="$(python3 -c 'import os,sys; print(os.path.abspath(sys.argv[1]))' "$PREFIX")"
BIN_DIR="${BIN_DIR:-$PREFIX/bin}"
BIN_DIR="$(python3 -c 'import os,sys; print(os.path.abspath(sys.argv[1]))' "$BIN_DIR")"
DATA_DIR="$PREFIX/share/konstructor"
LAUNCHER="$BIN_DIR/konstructor"

run() {
  printf '+ '
  printf '%q ' "$@"
  printf '\n'
  (( DRY_RUN )) || "$@"
}

if (( UNINSTALL )); then
  run rm -rf "$DATA_DIR" "$LAUNCHER"
  echo "Konstructor removido de $PREFIX."
  exit 0
fi

[[ -d "$SOURCE_DIR/konstructor" ]] || { echo "diretório konstructor ausente em $SOURCE_DIR" >&2; exit 1; }
run mkdir -p "$DATA_DIR" "$BIN_DIR"
run rm -rf "$DATA_DIR/konstructor"
run cp -R "$SOURCE_DIR/konstructor" "$DATA_DIR/konstructor"
run cp "$SOURCE_DIR/README.md" "$DATA_DIR/README.md"
if (( ! DRY_RUN )); then
  cat > "$LAUNCHER" <<EOF
#!/usr/bin/env bash
set -euo pipefail
export PYTHONPATH="${DATA_DIR}\${PYTHONPATH:+:\$PYTHONPATH}"
exec python3 -m konstructor "\$@"
EOF
  chmod 0755 "$LAUNCHER"
else
  echo "+ create $LAUNCHER (launcher)"
fi

echo "Instalação concluída. Execute: $LAUNCHER tui /caminho/kodoo"
