#!/usr/bin/env bash
# Installa la voce di crontab utente che promuove a 'passato' gli

set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WRAPPER="$PROJECT_DIR/cron_mark_passed_events.sh"
MARKER="# timesheetcompiler:mark_passed_events"
SCHEDULE="0 3 8-12 * *"

if [ ! -x "$WRAPPER" ]; then
  echo "Wrapper mancante o non eseguibile: $WRAPPER" >&2
  exit 1
fi

CRON_TZ="$(date +%Z)"
echo "Timezone di sistema: $CRON_TZ"
echo "Schedule: $SCHEDULE  ->  $WRAPPER"

# Backup della crontab attuale prima di riscriverla: questo script sostituisce
# l'intera crontab dell'utente, quindi serve una via di ritorno.
BACKUP_DIR="$PROJECT_DIR/logs"
mkdir -p "$BACKUP_DIR"
BACKUP_FILE="$BACKUP_DIR/crontab.bak.$(date +%Y%m%d%H%M%S)"
crontab -l > "$BACKUP_FILE" 2>/dev/null || : > "$BACKUP_FILE"
echo "Backup della crontab attuale: $BACKUP_FILE"
echo "Per ripristinarla:  crontab $BACKUP_FILE"

# Rimuove l'eventuale voce precedente (riga marker + riga del wrapper) e riscrive in coda quella nuova.
CURRENT="$(cat "$BACKUP_FILE")"
# Si filtra sul *nome* dello script, non sul percorso completo: cosi' una voce
# installata da una copia precedente del progetto viene sostituita
WRAPPER_NAME="$(basename "$WRAPPER")"
CLEANED="$(printf '%s\n' "$CURRENT" | grep -v -F "$MARKER" | grep -v -F "$WRAPPER_NAME" || true)"

{
  printf '%s\n' "$CLEANED" | sed '/^$/d'
  echo "$MARKER"
  echo "$SCHEDULE $WRAPPER"
} | crontab -

echo "Installato. Voce attuale:"
crontab -l | grep -A1 -F "$MARKER"
echo
echo "Log: $PROJECT_DIR/logs/mark_passed_events.log"
echo "Prova senza scrittura:"
echo "  $WRAPPER --dry-run && tail -5 $PROJECT_DIR/logs/mark_passed_events.log"
