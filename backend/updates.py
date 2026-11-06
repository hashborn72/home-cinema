"""Public release metadata, immutable APK paths, no build tools on the server."""
import hashlib
import json
import os
import re
from functools import lru_cache
from pathlib import Path
from fastapi import HTTPException
from settings import public_url

APK_NAME = re.compile(r'home-cinema-[1-9][0-9]*-[a-f0-9]{64}\.apk')


def releases_dir():
    return Path(os.environ.get('CINEMA_RELEASES', '/releases'))


def release_file(filename):
    if not APK_NAME.fullmatch(filename):
        raise HTTPException(404, 'Release not found')
    path = releases_dir() / filename
    if not path.is_file() or path.is_symlink():
        raise HTTPException(404, 'Release not found')
    return path


@lru_cache(maxsize=8)
def checksum(path, size, mtime):
    with open(path, 'rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def latest_release():
    try:
        raw = (releases_dir() / 'latest.json').read_bytes()
        if len(raw) > 32768:
            raise ValueError('Large manifest')
        info = json.loads(raw)
        code, name, sha, size, notes = (info[k] for k in ('versionCode','versionName','sha256','size','changes'))
        if (type(code) is not int or code <= 0 or type(size) is not int or size <= 0
                or not isinstance(name,str) or not 1 <= len(name) <= 80
                or not isinstance(sha,str) or not re.fullmatch('[a-f0-9]{64}',sha)
                or not isinstance(notes,str) or len(notes) > 12000
                or type(info['minSdk']) is not int or info['minSdk'] < 23):
            raise ValueError('Invalid manifest')
        filename = f'home-cinema-{code}-{sha}.apk'
        path = release_file(filename)
        stat = path.stat()
        if stat.st_size != size or checksum(str(path),size,stat.st_mtime_ns) != sha:
            raise ValueError('Incomplete release')
        return dict(versionCode=code,versionName=name,changes=notes,sha256=sha,size=size,
                    minSdk=info['minSdk'],apkUrl=public_url()+'/downloads/'+filename)
    except (OSError,ValueError,KeyError,TypeError,HTTPException):
        raise HTTPException(503,'No verified release published') from None
