#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
source ../.venv/bin/activate
python manage.py dumpdata \
  --natural-foreign \
  --natural-primary \
  --exclude contenttypes \
  --exclude auth.permission \
  --exclude admin.logentry \
  --exclude sessions.session \
  --indent 2 \
  -o dump.json
ls -lh dump.json
python - <<'PY'
import json, collections
d = json.load(open('dump.json'))
for m, n in sorted(collections.Counter(o['model'] for o in d).items()):
    print(f'{n:5d}  {m}')
PY
