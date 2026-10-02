from __future__ import annotations

import ssl
from urllib.request import urlopen

import certifi


def open_url(url: str, *, timeout: int = 30):
    context = ssl.create_default_context(cafile=certifi.where())
    return urlopen(url, timeout=timeout, context=context)
