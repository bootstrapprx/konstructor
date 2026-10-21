#!/usr/bin/env bash
set -euo pipefail

SOURCE_DIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
if [[ "${EUID:-$(id -u)}" -eq 0 ]]; then DEFAULT_PREFIX="/usr/local"; else DEFAULT_PREFIX="${HOME}/.local"; fi
PREFIX="$DEFAULT_PREFIX"
BIN_DIR=""
DRY_RUN=0
UNINSTALL=0
ADD_TO_PATH=0
WORKSPACE_INPUT=""
WORKSPACE_SET=0

usage() {
  cat <<'USAGE'
Uso: ./install.sh [opções]

Opções:
  --prefix DIR       prefixo de instalação (padrão: /usr/local como root, ~/.local caso contrário)
  --bin-dir DIR      diretório do launcher (padrão: PREFIX/bin)
  --workspace DIR    configura workspace Kodoo ou caminho de app existente
  --dry-run          exibe ações sem escrever arquivos
  --add-to-path      adiciona BIN_DIR ao rc do Bash ou Zsh (opcional)
  --uninstall        remove a instalação correspondente ao prefixo
  -h, --help         exibe esta ajuda
USAGE
}

while (($#)); do
  case "$1" in
    --prefix) [[ $# -ge 2 ]] || { echo "--prefix exige um diretório" >&2; exit 2; }; PREFIX="$2"; shift 2;;
    --bin-dir) [[ $# -ge 2 ]] || { echo "--bin-dir exige um diretório" >&2; exit 2; }; BIN_DIR="$2"; shift 2;;
    --workspace) [[ $# -ge 2 ]] || { echo "--workspace exige um diretório" >&2; exit 2; }; WORKSPACE_INPUT="$2"; WORKSPACE_SET=1; shift 2;;
    --dry-run) DRY_RUN=1; shift;;
    --uninstall) UNINSTALL=1; shift;;
    --add-to-path) ADD_TO_PATH=1; shift;;
    -h|--help) usage; exit 0;;
    *) echo "opção desconhecida: $1" >&2; usage >&2; exit 2;;
  esac
done

PREFIX="$(python3 -c 'import os,sys; print(os.path.abspath(sys.argv[1]))' "$PREFIX")"
BIN_DIR="${BIN_DIR:-$PREFIX/bin}"
BIN_DIR="$(python3 -c 'import os,sys; print(os.path.abspath(sys.argv[1]))' "$BIN_DIR")"
DATA_DIR="$PREFIX/share/konstructor"
LAUNCHER="$BIN_DIR/konstructor"
find_kodoo_root() {
  python3 - "$1" <<'PY'
from pathlib import Path
import sys

start = Path(sys.argv[1]).expanduser().resolve()
for candidate in (start, *start.parents):
    if ((candidate / "Cargo.toml").is_file()
            and "[workspace]" in (candidate / "Cargo.toml").read_text(encoding="utf-8", errors="replace")
            and (candidate / "scripts/standard.py").is_file()
            and (candidate / "modules/_template").is_dir()
            and (candidate / "apps").is_dir()):
        print(candidate)
        break
PY
}
KODOO_ROOT=""
if (( WORKSPACE_SET )); then
  KODOO_ROOT="$(find_kodoo_root "$WORKSPACE_INPUT")"
  [[ -n "$KODOO_ROOT" ]] || { echo "workspace Kodoo/app inválido: $WORKSPACE_INPUT" >&2; exit 2; }
else
  for candidate in "$PWD" "$SOURCE_DIR/../kodoo"; do
    KODOO_ROOT="$(find_kodoo_root "$candidate")"
    [[ -n "$KODOO_ROOT" ]] && break
  done
  if [[ -z "$KODOO_ROOT" && -f "$DATA_DIR/workspace" ]]; then
    KODOO_ROOT="$(find_kodoo_root "$(<"$DATA_DIR/workspace")")"
  fi
  if [[ -t 0 ]]; then
    if [[ -n "$KODOO_ROOT" ]]; then
      prompt="Kodoo ou app existente [$KODOO_ROOT] (Enter aceita, outro caminho altera): "
    else
      prompt="Caminho do workspace Kodoo ou de um app existente (vazio ignora): "
    fi
    read -r -p "$prompt" selected
    if [[ -n "$selected" ]]; then
      KODOO_ROOT="$(find_kodoo_root "$selected")"
      [[ -n "$KODOO_ROOT" ]] || { echo "workspace Kodoo/app inválido: $selected" >&2; exit 2; }
    fi
  fi
fi
SHELL_RC=""
if (( ADD_TO_PATH )); then
  case "${SHELL:-}" in
    */bash|bash) SHELL_RC="${HOME}/.bashrc";;
    */zsh|zsh) SHELL_RC="${HOME}/.zshrc";;
    *) echo "--add-to-path suporta Bash e Zsh; SHELL=${SHELL:-indefinido}" >&2; exit 2;;
  esac
fi

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
run python3 -m pip install --upgrade --target "$DATA_DIR/vendor" 'textual>=1,<9'
run rm -rf "$DATA_DIR/konstructor"
run cp -R "$SOURCE_DIR/konstructor" "$DATA_DIR/konstructor"
run cp "$SOURCE_DIR/README.md" "$DATA_DIR/README.md"
if [[ -n "$KODOO_ROOT" ]]; then
  printf '+ configure workspace %s\n' "$KODOO_ROOT"
  if (( ! DRY_RUN )); then printf '%s\n' "$KODOO_ROOT" > "$DATA_DIR/workspace"; fi
else
  run rm -f "$DATA_DIR/workspace"
fi
if (( ! DRY_RUN )); then
  cat > "$LAUNCHER" <<EOF
#!/usr/bin/env bash
set -euo pipefail
export PYTHONPATH="${DATA_DIR}/vendor:${DATA_DIR}\${PYTHONPATH:+:\$PYTHONPATH}"
if [[ -z "\${KONSTRUCTOR_WORKSPACE:-}" && -f "${DATA_DIR}/workspace" ]]; then
  export KONSTRUCTOR_WORKSPACE="\$(<"${DATA_DIR}/workspace")"
fi
exec python3 -m konstructor "\$@"
EOF
  chmod 0755 "$LAUNCHER"
else
  echo "+ create $LAUNCHER (launcher)"
fi

if (( ADD_TO_PATH )); then
  PATH_ENTRY="$(python3 -c 'import shlex,sys; print("export PATH=" + shlex.quote(sys.argv[1]) + ":\"$PATH\"")' "$BIN_DIR")"
  if [[ -f "$SHELL_RC" ]] && grep -Fqx -- "$PATH_ENTRY" "$SHELL_RC"; then
    echo "PATH já configurado em $SHELL_RC."
  else
    printf '+ append %s\n' "$SHELL_RC"
    if (( ! DRY_RUN )); then printf '\n%s\n' "$PATH_ENTRY" >> "$SHELL_RC"; fi
  fi
  printf 'Instalação concluída. Reabra o terminal ou execute: source %q\n' "$SHELL_RC"
elif [[ ":${PATH:-}:" == *":$BIN_DIR:"* ]]; then
  echo "Instalação concluída."
else
  echo "Instalação concluída."
  echo "Para adicionar $BIN_DIR ao PATH do Bash/Zsh, reexecute com --add-to-path."
fi
if [[ -n "$KODOO_ROOT" ]]; then
  echo "Workspace Kodoo configurado: $KODOO_ROOT"
  if (( ADD_TO_PATH )) || [[ ":${PATH:-}:" == *":$BIN_DIR:"* ]]; then
    echo "Use: konstructor tui"
  else
    printf 'Use: %q tui\n' "$LAUNCHER"
  fi
else
  echo "Workspace não configurado. Use --workspace DIR ou informe o caminho ao executar o Konstructor."
  if (( ADD_TO_PATH )) || [[ ":${PATH:-}:" == *":$BIN_DIR:"* ]]; then
    echo "Exemplo: konstructor tui /caminho/kodoo"
  else
    printf 'Exemplo: %q tui /caminho/kodoo\n' "$LAUNCHER"
  fi
fi
