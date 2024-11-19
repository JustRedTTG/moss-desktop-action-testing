import asyncio
import json
import logging
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from hashlib import sha256
from traceback import print_exc
from typing import Dict, List

import requests
import colorama
from requests.adapters import HTTPAdapter
from urllib3 import Retry

from rm_api.auth import get_token, refresh_token
from rm_api.models import DocumentCollection, Document, Metadata, Content, make_uuid, File, make_hash
from rm_api.notifications import handle_notifications
from rm_api.notifications.models import FileSyncProgress, SyncRefresh, DocumentSyncProgress, NewDocuments
from rm_api.storage.common import get_document_storage_uri, get_document_notifications_uri
from rm_api.storage.new_sync import get_documents_new_sync, handle_new_api_steps
from rm_api.storage.old_sync import get_documents_old_sync, update_root, RootUploadFailure
from rm_api.storage.new_sync import get_root as get_root_new
from rm_api.storage.old_sync import get_root as get_root_old
from rm_api.storage.v3 import get_documents_using_root, get_file, get_file_contents, make_files_request, put_file, \
    check_file_exists

colorama.init()


class API:
    document_collections: Dict[str, DocumentCollection]
    documents: Dict[str, Document]

    def __init__(self, require_token: bool = True, token_file_path: str = 'token', sync_file_path: str = 'sync',
                 uri: str = None, discovery_uri: str = None, author_id: str = None, log_file='rm_api.log'):
        self.retry_strategy = Retry(
            total=10,
            backoff_factor=0.5,
            status_forcelist=(429,)
        )
        http_adapter = HTTPAdapter(max_retries=self.retry_strategy)
        self.session = requests.Session()
        self.session.mount("http://", http_adapter)
        self.session.mount("https://", http_adapter)

        self.token_file_path = token_file_path
        if not author_id:
            self.author_id = make_uuid()
        else:
            self.author_id = author_id
        self.uri = uri or os.environ.get("URI", "https://webapp.cloud.remarkable.com/")
        self.discovery_uri = discovery_uri or os.environ.get("DISCOVERY_URI",
                                                             "https://service-manager-production-dot-remarkable-production.appspot.com/")
        self.sync_file_path = sync_file_path
        if self.sync_file_path is not None:
            os.makedirs(self.sync_file_path, exist_ok=True)
        self.document_storage_uri = None
        self.document_notifications_uri = None
        self._upload_lock = threading.Lock()
        self._hook_list = {}  # Used for event hooks
        self._use_new_sync = False
        # noinspection PyTypeChecker
        self.document_collections = {}
        # noinspection PyTypeChecker
        self.documents = {}
        self._token = None
        self.debug = False
        self.ignore_error_protection = False
        self.connected_to_notifications = False
        self.require_token = require_token
        if not self.uri.endswith("/"):
            self.uri += "/"
        if not self.discovery_uri.endswith("/"):
            self.discovery_uri += "/"
        token = os.environ.get("TOKEN")
        if token is None:
            if os.path.exists(self.token_file_path):
                self.token = open(self.token_file_path).read()
            else:
                self.get_token()
        else:
            self.token = token

        self.log_file = log_file
        self.log_lock = threading.Lock()

        # Set up logging configuration
        logging.basicConfig(filename=self.log_file, level=logging.INFO,
                            format='%(asctime)s - %(message)s',
                            filemode='a')  # 'a' for append mode
        self.loop = asyncio.get_event_loop()

    @property
    def use_new_sync(self):
        return self._use_new_sync

    def connect_to_notifications(self):
        if self.connected_to_notifications:
            return
        self.check_for_document_notifications()
        handle_notifications(self)
        self.connected_to_notifications = True

    @use_new_sync.setter
    def use_new_sync(self, value):
        if not self._use_new_sync and value:
            handle_new_api_steps(self)
        self._use_new_sync = value

    @property
    def token(self):
        return self._token

    @token.setter
    def token(self, value):
        if not value:
            return
        token = refresh_token(self, value)
        self.session.headers["Authorization"] = f"Bearer {token}"
        self._token = token

    def get_token(self, code: str = None):
        self.token = get_token(self, code)

    def get_documents(self, progress=lambda d, i: None):
        self.check_for_document_storage()
        if self.use_new_sync:
            get_documents_new_sync(self, progress)
        else:
            get_documents_old_sync(self, progress)

    def get_root(self):
        self.check_for_document_storage()
        if self.use_new_sync:
            return get_root_new(self)
        else:
            return get_root_old(self)

    def spread_event(self, event: object):
        for hook in self._hook_list.values():
            hook(event)

    def add_hook(self, hook_id, hook):
        self._hook_list[hook_id] = hook

    def remove_hook(self, hook_id):
        del self._hook_list[hook_id]

    def check_for_document_storage(self):
        if not self.document_storage_uri:
            uri = get_document_storage_uri(self)
            if not uri:
                return
            elif uri == 'local.appspot.com':
                uri = self.uri
            else:
                if not uri.endswith("/"):
                    uri += "/"
                uri = f'https://{uri}'

            self.document_storage_uri = uri

    def upload(self, document: Document):
        upload_event = FileSyncProgress()
        self.spread_event(upload_event)
        try:
            document.ensure_download()
            with self._upload_lock:
                self._upload_document_contents([document], upload_event)
        finally:
            upload_event.finished = True

    def upload_many_documents(self, documents: List[Document], callback=None):
        upload_event = FileSyncProgress()
        self.spread_event(upload_event)
        try:
            for document in documents:
                document.ensure_download()
            with self._upload_lock:
                self._upload_document_contents(documents, upload_event)
        except:
            print_exc()
        finally:
            upload_event.finished = True

    def _upload_document_contents(self, documents: List[Document], progress: FileSyncProgress):
        # We need to upload the content, metadata, rm file, file list and update root
        # This is the order that remarkable expects the upload to happen in, anything else and they might detect it as
        # API tampering, so we wanna follow their upload cycle
        progress.total += 2  # Getting root / Uploading root

        root = self.get_root()  # root info

        _, files = get_file(self, root['hash'])
        progress.done += 1  # Got root

        new_root = {
            "broadcast": True,
            "generation": root['generation']
        }

        document_files = [
            File(
                None,
                document.uuid,
                len(document.files), 0,
                f"{document.uuid}.docSchema"
            )
            for document in documents
        ]

        uuids = [document.uuid for document in documents]
        new_root_files = document_files + [
            file
            for file in files
            if file.uuid not in uuids
        ]

        old_files = []
        files_with_changes = []

        for document in documents:
            document.export()
            document.provision = True
        self.documents.update({
            document.uuid: document
            for document in documents
        })
        self.spread_event(NewDocuments())

        # Figure out what files have changed
        progress.total += sum(len(document.files) for document in documents)
        for document in documents:
            for file in document.files:
                try:
                    exists = check_file_exists(self, file.hash, binary=True, use_cache=False)
                    if not exists:
                        files_with_changes.append(file)
                    else:
                        old_files.append(file)
                except:
                    files_with_changes.append(file)
                finally:
                    progress.done += 1

        # Copy the content data so we can add more files to it
        content_datas = {}
        for document in documents:
            content_datas.update(document.content_data.copy())

        # Update the hash for files that have changed
        for file in files_with_changes:
            file.hash = make_hash(content_datas[file.uuid])
            file.size = len(content_datas[file.uuid])

        # Make a new document file with the updated files for this document

        for document, document_file in zip(documents, document_files):
            document_file_content = ['3\n']
            document_file_hash = sha256()
            for file in sorted(document.files, key=lambda file: file.uuid):
                file.hash = make_hash(content_datas[file.uuid])
                document_file_hash.update(bytes.fromhex(file.hash))
                file.size = len(content_datas[file.uuid])
                document_file_content.append(file.to_line())

            document_file_content = ''.join(document_file_content).encode()
            document_file.hash = document_file_hash.hexdigest()
            document_file.size = len(document_file_content)

            # Add the document file to the content_data
            content_datas[document_file.uuid] = document_file_content
            files_with_changes.append(document_file)

        # Prepare the root file
        root_file_content = ['3\n']
        for file in new_root_files:
            root_file_content.append(file.to_root_line())

        root_file_content = ''.join(root_file_content).encode()
        root_file = File(make_hash(root_file_content), f"root.docSchema", len(new_root_files), len(root_file_content))
        new_root['hash'] = root_file.hash

        files_with_changes.append(root_file)
        content_datas[root_file.uuid] = root_file_content

        # Upload all the files that have changed
        document_operations = {}

        for document in documents:
            document_sync_operation = DocumentSyncProgress(document.uuid, progress)
            document_operations[document.uuid] = document_sync_operation
            self.spread_event(document_sync_operation)

        futures = []
        with ThreadPoolExecutor() as executor:
            loop = asyncio.new_event_loop()  # Get the current event loop
            for file in files_with_changes:
                if (document_uuid := file.uuid.split('/')[0].split('.')[0]) in document_operations:
                    document_operation = document_operations[document_uuid]
                else:
                    document_operations[file.uuid] = DocumentSyncProgress(file.uuid, progress)
                    document_operation = document_operations[file.uuid]

                # This is where you use run_in_executor to call your async function in a separate thread
                future = loop.run_in_executor(executor, put_file, self, file, content_datas[file.uuid],
                                              document_operation)
                futures.append(future)
            executor.shutdown(wait=True)


        # Wait for operation to finish
        while not all(operation.finished for operation in document_operations.values()):
            time.sleep(.1)

        # Update the root
        try:
            update_root(self, new_root)
        except RootUploadFailure:
            self.log("Sync root failed, this is fine if you decided to sync on another device / start a secondary sync")
            progress.done = 0
            progress.total = 0
            self._upload_document_contents(documents, progress)
        progress.done += 1  # Update done finally matching done/total

        for document in documents:
            document.content_data.clear()
            document.files_available = document.check_files_availability()
            document.provision = False

        self.spread_event(SyncRefresh())

    def check_for_document_notifications(self):
        if not self.document_notifications_uri:
            uri = get_document_notifications_uri(self)
            if not uri:
                return
            elif uri == 'local.appspot.com':
                uri = self.uri
            else:
                if not uri.endswith("/"):
                    uri += "/"
                uri = f'https://{uri}'
            self.document_notifications_uri = uri

    def log(self, *args):
        with self.log_lock:
            if self.debug:
                print(*args)
            logging.info(' '.join(map(str, args)))
