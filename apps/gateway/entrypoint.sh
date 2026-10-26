#!/bin/sh
set -e
python - <<'PY'
import os, time
from sqlalchemy import create_engine, text

url = os.environ["DATABASE_URL"]
for _ in range(60):
    try:
        with create_engine(url).connect() as conn:
            conn.execute(text("select 1"))
        break
    except Exception:
        time.sleep(2)
else:
    raise SystemExit("database not ready")
PY
alembic upgrade head
exec python server.py
