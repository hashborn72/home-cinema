#!/usr/bin/env python3
"""Run on Autobot after apksigner verification; publish manifest last, atomically."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile


def atomic_bytes(path, data):
    fd, temporary = tempfile.mkstemp(prefix='.'+path.name,dir=path.parent)
    try:
        with os.fdopen(fd,'wb') as output:
            output.write(data); output.flush(); os.fsync(output.fileno())
        os.chmod(temporary,0o644)
        os.replace(temporary,path)
    finally:
        if os.path.exists(temporary): os.unlink(temporary)


def publish(apk, notes, aapt):
    badging=subprocess.check_output([str(aapt),'dump','badging',str(apk)],text=True)
    package=re.search(r"package: name='([^']+)' versionCode='(\d+)' versionName='([^']+)'",badging)
    sdk=re.search(r"sdkVersion:'(\d+)'",badging)
    if not package or package[1]!='space.hashborn.cinema' or not sdk:
        raise ValueError('Wrong application')
    digest=hashlib.sha256(apk.read_bytes()).hexdigest()
    code=int(package[2])
    info=dict(versionCode=code,versionName=package[3],minSdk=int(sdk[1]),
              changes=notes.read_text(encoding='utf-8').strip(),size=apk.stat().st_size,sha256=digest)
    if not info['changes'] or len(info['changes'])>12000:
        raise ValueError('Release notes required')
    target=apk.parent/f'home-cinema-{code}-{digest}.apk'
    if target.exists() and hashlib.sha256(target.read_bytes()).hexdigest()!=digest:
        raise ValueError('Immutable release changed')
    atomic_bytes(target,apk.read_bytes())
    atomic_bytes(apk.parent/'home-cinema.apk',apk.read_bytes())
    atomic_bytes(apk.parent/'latest.json',(json.dumps(info,ensure_ascii=False,indent=2)+'\n').encode())
    print(json.dumps(info,ensure_ascii=True))

if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('apk',type=Path)
    parser.add_argument('notes',type=Path)
    parser.add_argument('aapt',type=Path)
    args=parser.parse_args()
    publish(args.apk,args.notes,args.aapt)
