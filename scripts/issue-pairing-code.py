#!/usr/bin/env python3
"""Run inside the backend: python /app/issue-pairing-code.py is NOT required.
Host usage: docker exec -i home-cinema-dev python - < scripts/issue-pairing-code.py
Output is a private enrollment credential. Never log or publish it on /install.
"""
from pathlib import Path
from pairing import Pairing

print(Pairing(Path('/data/library.sqlite3')).issue('Android TV'))
