#!/bin/bash
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"

echo "[*] Остановка контейнеров..."
docker compose down

echo "[✓] Контейнеры остановлены."
echo "    Данные в ./config/ сохранены."
echo "    Для полного удаления: docker compose down -v && rm -rf config/"