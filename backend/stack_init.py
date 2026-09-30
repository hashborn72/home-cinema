"""Offline, repeatable first-start provisioning. Private imports are optional."""
import json
import os
from pathlib import Path
import secrets
import sqlite3


def private_write(path, contents):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'w') as out:
        out.write(contents)


def seeds(source):
    sites = {'lostfilm': 'https://www.lostfilm.tv/', 'exkinoray': 'https://exkinoray.ru/', 'rutor': 'https://rutor.info/'}
    result = [{'id':'sitelink','type':'inputstring','name':'Site Link','value':sites[source]}]
    if source != 'lostfilm':
        result += [{'id':'stripcyrillicletters','type':'inputbool','value':False},
                   {'id':'addrustoendofalltitlestoimprovelanguagedetectionbysonarrandradarr.willcauseenglish-onlyresultstobemisidentified.','type':'inputbool','value':False}]
    if source == 'exkinoray':
        result += [{'id':'sortrequestedfromsite','type':'inputselect','value':'4'},
                   {'id':'orderrequestedfromsite','type':'inputselect','value':'desc'}]
    if source == 'rutor':
        result += [{'id':'sortrequestedfromsite(appliesonlytosearchwithkeywords)','type':'inputselect','value':'0'}]
    return result


def initialize(data, jackett, torrserver, imports, owner=None):
    for folder in (data, jackett, torrserver, jackett/'Jackett', jackett/'Jackett/Indexers'):
        folder.mkdir(parents=True, exist_ok=True, mode=0o700)
    config_path = jackett/'Jackett/ServerConfig.json'
    if not config_path.exists():
        imported = imports/'Jackett/ServerConfig.json'
        config = json.loads(imported.read_text()) if imported.exists() else {}
        config.update(Port=9117, AllowExternal=True, AllowCORS=True, UpdateDisabled=True,
                      BasePathOverride='', BaseUrlOverride='')
        config['APIKey'] = config.get('APIKey') or secrets.token_hex(16)
        private_write(config_path, json.dumps(config))
    config = json.loads(config_path.read_text())
    if not config.get('APIKey'):
        raise ValueError('Existing Jackett config has no API key; refusing to replace it')
    key_path = data/'jackett-key'
    # Keep backend in sync with the persistent Jackett credential, never print it.
    if not key_path.exists() or key_path.read_text().strip() != config['APIKey']:
        temporary = data/'jackett-key.new'
        with temporary.open('w') as out: out.write(config['APIKey'])
        temporary.chmod(0o600)
        temporary.replace(key_path)
    for source in ('lostfilm','exkinoray','rutor'):
        path = jackett/'Jackett/Indexers'/f'{source}.json'
        imported = imports/'Jackett/Indexers'/f'{source}.json'
        if not path.exists():
            value = json.loads(imported.read_text()) if imported.exists() else seeds(source)
            private_write(path, json.dumps(value))
    for name in ('device-token','tmdb-token'):
        source = imports/name
        if source.is_file() and not (data/name).exists():
            private_write(data/name, source.read_text().strip())
    source = imports/'library.sqlite3'
    target = data/'library.sqlite3'
    if source.is_file() and not target.exists():
        with sqlite3.connect(f'file:{source}?mode=ro', uri=True) as old, sqlite3.connect(target) as new:
            old.backup(new)
            if new.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                raise ValueError('Imported library failed integrity check')
        target.chmod(0o600)
    if owner is not None:
        for root in (data, jackett, torrserver):
            os.chown(root, *owner)
            root.chmod(0o700)
            for path in root.rglob('*'):
                if path.is_symlink(): raise ValueError('Symlink in persistent data')
                os.chown(path, *owner)
    print('Persistent configuration ready. Tracker accounts, if required, are private imports or configured in Jackett UI.')


if __name__ == '__main__':
    initialize(Path('/data'), Path('/jackett'), Path('/torrserver'), Path('/import'), (65532,65532))
