"""Deployment endpoints; internal service DNS must never reach the TV."""
import os
from urllib.parse import urlsplit


def endpoint(name, default):
    value = os.environ.get(name, default).rstrip('/')
    parts = urlsplit(value)
    if (parts.scheme not in ('http', 'https') or not parts.hostname or
            parts.username or parts.password or parts.query or parts.fragment or parts.path):
        raise ValueError('Invalid endpoint: ' + name)
    return value


def public_url():
    return endpoint('CINEMA_PUBLIC_URL', 'http://192.168.0.221:18093')


def jackett_url():
    return endpoint('JACKETT_URL', 'http://192.168.1.144:8091')


def torrserver_url():
    return endpoint('TORRSERVER_URL', 'http://192.168.1.144:8090')
