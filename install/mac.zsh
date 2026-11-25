#!/bin/zsh
set -euo pipefail
if [[ "$(uname -s)" != Darwin || "$(/usr/sbin/sysctl -n hw.optional.arm64 2>/dev/null || true)" != 1 ]]; then
  print -u2 'Нужен Mac с Apple Silicon (M1 и новее).'
  exit 1
fi
g1_root="${G1_INSTALL_DIR:-$HOME/Library/Application Support/G1AcqPlus}"
g1_source="${0:A:h:h}"
g1_free="$(df -k "$HOME" | awk 'NR==2{print $4}')"
if (( g1_free < 2 * 1024 * 1024 )); then
  print -u2 'Нужно минимум 2 ГиБ для среды Python; при новой установке MuMu — минимум 8 ГиБ.'
  exit 1
fi
mkdir -p "$g1_root/app" "$g1_root/install" "$g1_root/tools" "$g1_root/downloads"
if [[ "$g1_source/app" != "$g1_root/app" ]]; then
  for g1_file in "$g1_source/app/"*(.N); do
    cp "$g1_file" "$g1_root/app/"
  done
  cp "${0:A}" "$g1_root/install/mac.zsh"
fi
export UV_PYTHON_INSTALL_DIR="$g1_root/managed-python" UV_CACHE_DIR="$g1_root/cache"
export PYTHONUTF8=1 PYTHONDONTWRITEBYTECODE=1
g1_uv="$g1_root/tools/uv"
if [[ ! -x "$g1_uv" ]]; then
  g1_archive="$g1_root/downloads/uv-0.13.10-mac.tar.gz"
  curl -fL --retry 2 --proto '=https' --proto-redir '=https' --max-time 300 \
    'https://github.com/astral-sh/uv/releases/download/0.13.10/uv-aarch64-apple-darwin.tar.gz' -o "$g1_archive"
  [[ "$(shasum -a 256 "$g1_archive" | awk '{print $1}')" == e419bf07533bdbf48795fc793601c834dd79db57a8c49ce8e25cfe3a8639958d ]] || { print -u2 'Ошибка контрольной суммы uv'; exit 1; }
  g1_stage="$(mktemp -d "${TMPDIR:-/tmp}/g1-uv.XXXXXX")"
  trap 'rm -rf -- "$g1_stage"' EXIT
  tar -xzf "$g1_archive" -C "$g1_stage"
  cp "$g1_stage/uv-aarch64-apple-darwin/uv" "$g1_uv"
  chmod +x "$g1_uv"
fi
[[ "$("$g1_uv" --version)" == 'uv 0.13.10'* ]] || { print -u2 'Неожиданная версия uv'; exit 1; }
print '[1/3] Отдельный Python 3.13 без системного установщика'
"$g1_uv" --no-config python install --no-bin --no-registry 3.13
g1_python="$("$g1_uv" --no-config python find --no-project --managed-python --no-python-downloads 3.13)"
[[ -x "$g1_python" ]] || { print -u2 "uv не вернул существующий Python: $g1_python"; exit 1; }
g1_env="$g1_root/venv/bin/python"
if [[ ! -x "$g1_env" ]]; then
  "$g1_uv" --no-config venv --python "$g1_python" --no-python-downloads "$g1_root/venv"
fi
[[ -x "$g1_env" ]] || { print -u2 "Python в окружении не создан: $g1_env"; exit 1; }
"$g1_uv" --no-config pip install --python "$g1_env" --require-hashes --only-binary ':all:' -r "$g1_root/app/requirements.lock"
"$g1_uv" --no-config pip check --python "$g1_env"
"$g1_env" "$g1_root/app/runtime_check.py"
print '[2/3] Повторный запуск'
printf '%s\n' '#!/bin/zsh' 'set -euo pipefail' 'export G1_INSTALL_DIR="${0:A:h}"' \
  'exec /bin/zsh "$G1_INSTALL_DIR/install/mac.zsh" "$@"' > "$g1_root/Start-G1.command"
chmod +x "$g1_root/Start-G1.command"
if [[ "${G1_RUNTIME_ONLY:-0}" == 1 ]]; then
  print -r -- "Runtime готов: $g1_env"
  exit 0
fi
print '[3/3] MuMu, Android root и каталог Google Play'
"$g1_env" "$g1_root/app/g1plus.py" --work "$g1_root" "$@"
