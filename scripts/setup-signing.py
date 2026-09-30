#!/usr/bin/env python3
"""Create the dedicated APK signing identity once, outside the repository."""
import os
from pathlib import Path
import secrets
import subprocess

directory = Path('/root/.local/share/home-cinema-signing')
directory.mkdir(parents=True, exist_ok=True, mode=0o700)
os.chmod(directory, 0o700)
store = directory / 'release.jks'
password = directory / 'store-password'
if store.exists() and password.exists():
    print('Using existing signing identity')
elif store.exists() or password.exists():
    raise SystemExit('Incomplete signing identity; manual recovery required')
else:
    value = secrets.token_urlsafe(48)
    fd = os.open(password, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    with os.fdopen(fd, 'w') as out: out.write(value)
    env = dict(os.environ, CINEMA_SIGN_PASSWORD=value)
    subprocess.run(['keytool','-genkeypair','-keystore',str(store),'-storetype','PKCS12',
                    '-alias','home-cinema','-keyalg','RSA','-keysize','3072','-validity','10950',
                    '-dname','CN=Home Cinema','-storepass:env','CINEMA_SIGN_PASSWORD',
                    '-keypass:env','CINEMA_SIGN_PASSWORD'], env=env, check=True)
    os.chmod(store, 0o600)
    print('Created dedicated APK signing identity; back up the private directory')
