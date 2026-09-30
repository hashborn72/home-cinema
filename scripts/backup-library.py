"""Consistent private SQLite backup. Database may contain sensitive source URLs."""
import os
import sqlite3
from datetime import datetime,timezone
from pathlib import Path

root=Path('/root/home-cinema/data')
target=root/'backups'
target.mkdir(mode=0o700,exist_ok=True)
path=target/('library-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')+'.sqlite3')
os.close(os.open(path,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600))
with sqlite3.connect(root/'library.sqlite3') as source,sqlite3.connect(path) as destination:
    source.backup(destination)
    assert destination.execute('PRAGMA integrity_check').fetchone()[0]=='ok'
os.chmod(path,0o600)
print('SQLite backup verified:',path.name)
