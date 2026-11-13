#!/usr/bin/env bash
# Windmill installer for macOS / Linux:  bash install.sh
set -e
HERE="$(cd "$(dirname "$0")" && pwd)"
PY="$(command -v python3 || command -v python || true)"
if [ -z "$PY" ]; then echo "Please install Python 3.9+ first (https://python.org) and run this again."; exit 1; fi
command -v git >/dev/null || { echo "Please install git first and run this again."; exit 1; }

BIN="$HOME/.local/bin"
mkdir -p "$BIN"
cat > "$BIN/windmill" <<EOF
#!/usr/bin/env bash
exec "$PY" "$HERE/windmill.py" "\$@"
EOF
chmod +x "$BIN/windmill"
case ":$PATH:" in *":$BIN:"*) ;; *)
  for rc in "$HOME/.bashrc" "$HOME/.zshrc"; do [ -f "$rc" ] && echo "export PATH=\"\$PATH:$BIN\"" >> "$rc"; done
  export PATH="$PATH:$BIN";;
esac
echo "  OK  'windmill' command installed in $BIN"
"$PY" "$HERE/windmill.py" setup
echo "  From now on, type:  windmill   (open a new terminal first if it isn't found)"
