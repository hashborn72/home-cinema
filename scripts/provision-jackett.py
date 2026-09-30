"""Read a Jackett key from stdin; never log it or embed it in source/APK."""
import os
from pathlib import Path
import sys

key = sys.stdin.read().strip()
if not key or len(key) > 256 or any(c.isspace() for c in key):
    raise SystemExit('Invalid Jackett key')
path = Path('/root/home-cinema/data/jackett-key')
fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
with os.fdopen(fd, 'w') as out:
    out.write(key)
os.chown(path, 65532, 65532)
print('Jackett key provisioned (value not logged).')
