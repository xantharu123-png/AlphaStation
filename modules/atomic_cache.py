"""Small atomic file publishers for disposable scanner caches and logs."""

import json
import os
import tempfile


def _replace_complete_file(path, write_payload):
    """Publish only a closed, complete same-filesystem temporary file."""
    path = os.fspath(path)
    parent = os.path.dirname(os.path.abspath(path))
    os.makedirs(parent, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=parent,
                                         prefix=f".{os.path.basename(path)}.",
                                         suffix=".tmp", delete=False) as handle:
            # Record the path before serialization: its error must not leave
            # an orphaned partial temporary file, or truncate the old target.
            temporary = handle.name
            write_payload(handle)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        temporary = None
    finally:
        if temporary is not None:
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass


def write_json_atomic(path, payload, *, default=None, indent=None):
    """Keep the previous JSON cache on serialization, flush, or replace error."""
    _replace_complete_file(path, lambda handle: json.dump(
        payload, handle, default=default, indent=indent))


def write_text_atomic(path, text):
    """Publish complete text; callers own any read/modify/write locking."""
    _replace_complete_file(path, lambda handle: handle.write(text))
