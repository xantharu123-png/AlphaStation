"""Execute the actual admin test-mail handler with offline browser primitives."""
import json
import re

import pytest

from test_frontend_scanner_lifecycle import SOURCE, node_run


FETCH_START = SOURCE.index("const rawFetch = window.fetch.bind(window);")
FETCH_WRAPPER = SOURCE[FETCH_START:SOURCE.index("\nfunction ", FETCH_START)]


def _handler():
    start = SOURCE.index("const sendTechnicalTestMail = async")
    end = SOURCE.index("\n    };", start) + len("\n    };")
    return SOURCE[start:end]


def _execute(scenario):
    handler = _handler()
    refs = sorted(set(re.findall(r"\b(\w+)\.current\b", handler)))
    prefix = SOURCE[SOURCE.index("function AdminTab()"):SOURCE.index(handler)]
    states = re.findall(r"const\s*\[\s*(\w+)\s*,\s*(set\w+)\s*\]\s*=\s*useState\(", prefix)
    used_setters = set(re.findall(r"\b(set[A-Z]\w*)\s*\(", handler))
    setters = sorted(setter for _, setter in states if setter in used_setters)
    declarations = "\n".join(
        f"let {name} = {'true' if name == 'technicalMailConfirm' else 'false'};"
        for name, _ in states
    )
    declarations += "\n" + "\n".join(f"const {name} = {{current:false}};" for name in refs)
    declarations += "\n" + "\n".join(
        f"const {name} = value => changes.push({{name:{json.dumps(name)},value}});"
        for name in setters
    )
    harness = r"""
const assert = require('assert/strict');
const API = 'http://offline.invalid', headers = {'Content-Type':'application/json'};
let changes = [], requests = [], confirmations = [], auditRefreshes = 0;
global.window = {confirm:text => {throw new Error('Native confirmation must not be used');}};
global.confirm = window.confirm;
const showToast = (text, type) => changes.push({name:'toast',value:text,type});
const fetchMailAudit = async () => {auditRefreshes++;};
function response(body, status = 200) {
    return {ok:status >= 200 && status < 300,status,json:async()=>body};
}
let responder = async()=>response({delivery_status:'accepted',status:'ok'});
global.fetch = async (url, init) => {
    requests.push({url,init});
    assert.equal(url,API+'/api/test-email');
    assert.equal(init.method,'POST');
    return responder();
};
function deferred() {let resolve; const promise = new Promise(r=>{resolve=r;}); return {promise,resolve};}
"""
    code = harness + "\n" + declarations + "\nwindow.fetch=global.fetch;\n" + FETCH_WRAPPER
    code += "\nglobal.fetch=window.fetch;\n" + handler + "\n" + "(async()=>{\n" + scenario + r"""
console.log(JSON.stringify({changes,requests,confirmations,auditRefreshes}));
})().catch(error=>{console.error(error);process.exitCode=1;});
"""
    return json.loads(node_run(code))


def _text(result):
    return " ".join(str(change["value"]) for change in result["changes"] if isinstance(change["value"], str))


def test_unconfirmed_or_cancelled_inline_confirmation_cannot_send_a_test_mail():
    result = _execute("technicalMailConfirm=false; await sendTechnicalTestMail();")
    assert result["requests"] == []
    assert result["confirmations"] == []


def test_synchronous_double_click_still_sends_exactly_one_post():
    result = _execute("""
const pending = deferred(); responder=()=>pending.promise;
const first = sendTechnicalTestMail();
const second = sendTechnicalTestMail();
assert.equal(requests.length,1);
pending.resolve(response({delivery_status:'accepted',status:'ok'}));
await Promise.all([first,second]);
""")
    assert len(result["requests"]) == 1
    assert result["confirmations"] == []


def test_acceptance_text_does_not_claim_inbox_delivery():
    result = _execute("await sendTechnicalTestMail();")
    assert len(result["requests"]) == 1
    text = _text(result).lower()
    assert "angenommen" in text
    assert "erfolgreich zugestellt" not in text
    assert "im posteingang angekommen" not in text


@pytest.mark.parametrize("scenario", [
    "responder=async()=>{throw new TypeError('network unavailable');};",
    "responder=async()=>{const error=new Error('timeout'); error.name='AbortError'; throw error;};",
    "responder=async()=>({ok:true,status:200,json:async()=>{throw new SyntaxError('invalid json');}});",
    "responder=async()=>response({delivery_status:'unexpected',status:'ok'});",
])
def test_uncertain_response_is_reported_without_automatic_retry(scenario):
    result = _execute(scenario + " await sendTechnicalTestMail();")
    assert len(result["requests"]) == 1
    assert result["confirmations"] == []
    text = _text(result).lower()
    assert "unbekannt" in text or "nicht eindeutig" in text or "unklar" in text
    assert "nicht erneut senden" in text
    assert "erfolgreich zugestellt" not in text


def test_confirmed_rejection_is_reported_as_not_accepted():
    result = _execute("responder=async()=>response({detail:{delivery_status:'not_sent'}},503); await sendTechnicalTestMail();")
    assert len(result["requests"]) == 1
    assert "nicht vom mailserver angenommen" in _text(result).lower()


def test_post_cannot_supply_another_recipient_or_start_any_scan():
    result = _execute("await sendTechnicalTestMail();")
    request = result["requests"][0]
    assert request["url"] == "http://offline.invalid/api/test-email"
    assert "body" not in request["init"]
    assert request["init"]["credentials"] == "include"
    assert result["auditRefreshes"] == 0


def test_busy_state_is_cleared_after_uncertain_outcome_without_another_post():
    result = _execute("responder=async()=>{throw new TypeError('timeout');}; await sendTechnicalTestMail();")
    busy = [change["value"] for change in result["changes"] if change["name"] == "setTechnicalMailBusy"]
    assert busy == [True, False]
    assert len(result["requests"]) == 1


def test_confirmation_is_an_accessible_inline_dialog_with_cancel_and_send():
    section_start = SOURCE.index('<section aria-label="Mailversand">')
    section = SOURCE[section_start:SOURCE.index('</section>', section_start)]
    assert 'role="dialog"' in section
    assert 'aria-label="Technische Testmail bestätigen"' in section
    assert "Einmal senden" in section and "Abbrechen" in section
    assert "setTechnicalMailConfirm(true)" in section
    assert "setTechnicalMailConfirm(false)" in section
    assert "onClick={sendTechnicalTestMail}" in section
    assert "window.confirm" not in _handler()
