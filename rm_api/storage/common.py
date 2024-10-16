from typing import TYPE_CHECKING

TEMP = "sync"
DOCUMENT_STORAGE_URL = "{0}service/json/1/document-storage?environment=production&group=auth0%7C5a68dc51cb30df3877a1d7c4&apiVer=2"

if TYPE_CHECKING:
    from rm_api import API


def get_document_storage(api: 'API'):
    response = api.session.get(DOCUMENT_STORAGE_URL.format(api.discovery_uri))
    host = response.json().get("Host")
    if not api.session.get(f"https://{host}/sync/v3/root").ok:
        api.use_new_sync = True
        return None
    return host