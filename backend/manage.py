"""docker compose exec backend python manage.py pair|backup"""
import argparse
import os
from pathlib import Path
import sqlite3
from datetime import datetime, timezone
from pairing import Pairing

parser = argparse.ArgumentParser()
parser.add_argument('action', choices=('pair','backup'))
parser.add_argument('--label', default='Android TV')
args = parser.parse_args()
data = Path(os.environ.get('CINEMA_DATA','/data'))
if args.action == 'pair':
    print(Pairing(data/'library.sqlite3').issue(args.label))
else:
    folder = data/'backups'
    folder.mkdir(mode=0o700, exist_ok=True)
    target = folder/('library-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')+'.sqlite3')
    fd = os.open(target, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    os.close(fd)
    with sqlite3.connect(data/'library.sqlite3') as source, sqlite3.connect(target) as destination:
        source.backup(destination)
        assert destination.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
    print(target)
