#!/usr/bin/env bash
# Wrapper invocato da cron per promuovere a 'passato' gli eventi scaduti.
#
# Esiste come script separato invece che come riga di crontab perche' cron gira
# con un ambiente minimo (nessun virtualenv attivo, PATH ridotto, cwd = $HOME):
# qui si fissano interprete e percorsi in modo esplicito.
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_PYTHON="$PROJECT_DIR/../.venv/bin/python"
LOG_DIR="$PROJECT_DIR/logs"
LOG_FILE="$LOG_DIR/mark_passed_events.log"

mkdir -p "$LOG_DIR"

# Tutto dentro il blocco redirezionato: un errore di percorso deve finire nel
# log, non nella mail locale di cron dove nessuno lo guarderebbe.
{
  echo "--- $(date '+%Y-%m-%d %H:%M:%S %Z') ---"
  if [ ! -x "$VENV_PYTHON" ]; then
    echo "ERRORE: interprete non trovato o non eseguibile: $VENV_PYTHON"
    exit 1
  fi
  "$VENV_PYTHON" "$PROJECT_DIR/manage.py" mark_passed_events "$@"
} >> "$LOG_FILE" 2>&1
