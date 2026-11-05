#!/usr/bin/env bash
set -euo pipefail
rm -f \
  "${HOME}/.local/bin/butterflies" \
  "${HOME}/.local/bin/insects" \
  "${HOME}/.local/bin/lee-insects-widget" \
  "${HOME}/.local/share/applications/lee-insects.desktop"
rm -rf "${HOME}/.local/share/lee-insects"
echo "Removed the program. Saved settings under ~/.config were left in place."
