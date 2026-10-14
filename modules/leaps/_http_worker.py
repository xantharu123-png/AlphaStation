"""Isolated one-attempt HTTP executable. Never import parent app or LEAPS.

Input <=64KiB via anonymous stdin; output <=8MiB+small header via stdout.
No exception text, request headers, secrets, URLs or provider diagnostics logged.
Parent enforces total startup/DNS/connect/headers/body deadline and observes exit.
"""
import json
import socket
import sys
from urllib.parse import urlsplit

LIMIT=8*1024*1024


def execute():
    raw=sys.stdin.buffer.read(65537)
    if len(raw)>65536:
        return {'error':'transport_error'},b''
    data=json.loads(raw)
    url=data['url']
    if data.get('offline_only') is True:
        # Deny before DNS, and at socket connect; a child cannot silently escape
        # the test parent's in-memory socket monkeypatch.
        if urlsplit(url).hostname not in ('127.0.0.1','::1','localhost'):
            return {'error':'offline_network_denied'},b''
        real_connect=socket.socket.connect
        def connect(self,address):
            if not isinstance(address,tuple) or address[0] not in ('127.0.0.1','::1'):
                raise OSError('offline network denied')
            return real_connect(self,address)
        socket.socket.connect=connect
    import requests
    from requests.adapters import HTTPAdapter
    from urllib3.exceptions import HTTPError
    headers=dict(data['headers'],**{'Accept-Encoding':'identity'})
    with requests.Session() as session:
        session.trust_env=False
        session.mount('https://',HTTPAdapter(max_retries=0))
        session.mount('http://',HTTPAdapter(max_retries=0))
        try:
            with session.get(url,headers=headers,timeout=(3,10),allow_redirects=False,stream=True) as response:
                if response.status_code!=200:
                    return {'status':response.status_code},b''
                # Reject BEFORE any decompression allocation. Identity's raw
                # body equals decoded body; compressed JSON is not silently lost.
                if response.headers.get('Content-Encoding','identity').strip().lower() not in ('','identity'):
                    return {'error':'unsupported_content_encoding'},b''
                body=bytearray()
                while True:
                    chunk=response.raw.read(min(65536,LIMIT+1-len(body)),decode_content=False)
                    if not chunk: break
                    body.extend(chunk)
                    if len(body)>LIMIT:
                        return {'error':'response_too_large'},b''
                return {'status':response.status_code},bytes(body)
        except (requests.ConnectionError,requests.Timeout,HTTPError):
            return {'error':'connection_failed'},b''


if __name__=='__main__':
    try:
        meta,body=execute()
    except Exception:
        meta,body={'error':'transport_error'},b''
    sys.stdout.buffer.write(json.dumps(meta,separators=(',',':')).encode()+b'\n'+body)
    sys.stdout.buffer.flush()
