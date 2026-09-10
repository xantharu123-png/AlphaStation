"""Bounded transport recovery for required BI GETs, scoped to one scan run.

Only code-owned reasons/counters enter diagnostics. Exceptions, response bodies,
tickers and URLs must never be copied into public transport evidence.
"""
from requests.exceptions import ConnectionError, SSLError, Timeout


class BITransportError(RuntimeError):
    def __init__(self, code, reason):
        self.code = code
        self.reason = reason
        super().__init__(code)


class BITransportStopped(RuntimeError):
    pass


class BITransport:
    def __init__(self, get, should_stop, sleep, diagnostics):
        self.get = get
        self.should_stop = should_stop
        self.sleep = sleep
        self.diagnostics = diagnostics
        diagnostics.update(transport_requests=0, transport_retries=0,
                           transport_recovered_incidents=0,
                           transport_retry_budget_exhausted=0,
                           transport_error_counts={})

    def json(self, url, *, params, timeout=15):
        d = self.diagnostics
        for attempt in range(3):
            if self.should_stop():
                raise BITransportStopped() from None
            if attempt:
                d["transport_retries"] += 1
            d["transport_requests"] += 1
            code = "scan_data_unavailable"
            retryable = False
            try:
                response = self.get(url, params=params, timeout=timeout)
                status = response.status_code
            except SSLError:
                reason = "tls_failure"
            except Timeout:
                reason, retryable = "timeout", True
            except ConnectionError:
                reason, retryable = "connection_failure", True
            except Exception:
                reason = "unexpected_failure"
            else:
                if status == 200:
                    try:
                        payload = response.json()
                    except ValueError:
                        reason, code = "malformed_json", "scan_data_invalid"
                    except Exception:
                        reason = "unexpected_failure"
                    else:
                        if attempt:
                            d["transport_recovered_incidents"] += 1
                        return payload
                elif status in (401, 403):
                    reason, code = "http_unauthorized", "scan_provider_unauthorized"
                elif status == 429:
                    reason, code = "http_rate_limited", "scan_provider_rate_limited"
                elif status == 408:
                    reason, retryable = "http_request_timeout", True
                elif isinstance(status, int) and 500 <= status <= 599:
                    reason = "http_server_error"
                    retryable = status in (500, 502, 503, 504)
                elif isinstance(status, int) and 400 <= status <= 499:
                    reason = "http_client_error"
                else:
                    reason = "http_unexpected_status"
            counts = d["transport_error_counts"]
            counts[reason] = counts.get(reason, 0) + 1
            if self.should_stop():
                raise BITransportStopped() from None
            if retryable and attempt < 2 and d["transport_retries"] < 20:
                self.sleep((0.5, 1.0)[attempt])
                continue
            if retryable and attempt < 2 and d["transport_retries"] >= 20:
                d["transport_retry_budget_exhausted"] += 1
            d["transport_error_reason"] = reason
            raise BITransportError(code, reason) from None
