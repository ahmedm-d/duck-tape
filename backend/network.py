"""The shared public-only, DNS-pinned HTTPS byte boundary."""
import hashlib

from . import repository


def fetch_public(request: dict, limits: dict) -> dict:
    if request.get('method', 'GET') != 'GET':
        raise ValueError('Public source fetch supports read-only GET requests')
    max_bytes = limits.get('max_bytes', repository.MAX_ARCHIVE)
    if type(max_bytes) is not int or not 0 < max_bytes <= repository.MAX_EXPANDED:
        raise ValueError('Invalid public fetch byte limit')
    url = request.get('url')
    if not isinstance(url, str):
        raise ValueError('A public HTTPS URL is required')
    data = repository.fetch_public(url, max_bytes)
    if len(data) > max_bytes:
        raise ValueError('Public source exceeds size limit')
    return {'data': data, 'provenance': {'url': url, 'transport': 'DNS-pinned HTTPS; redirects revalidated',
                                       'size': len(data), 'sha256': hashlib.sha256(data).hexdigest()}}
