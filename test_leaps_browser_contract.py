import json
import os
from pathlib import Path
from tests.fixtures.leaps.final_browser import export


def test_actual_backend_public_browser_contract(tmp_path):
    data=export(tmp_path)
    cases=data['cases']
    supported=cases['supported']['results']['rows'][0]
    assert supported['company_name']=='Synthetic Verified Company'
    assert supported['underlying']['indicators']['completed_close']=='12.19'
    assert supported['underlying']['facts']['profile']=='operating_company'
    assert supported['filter_status']=='passed'
    bank=cases['bank']['results']['rows'][0]
    assert bank['underlying']['facts']['profile']=='financial'
    assert bank['underlying']['coverage']['history']=='missing'
    assert bank['filter_status']=='missing_evidence'
    assert cases['excluded']['results']['rows'][0]['filter_status']=='excluded'
    capital=cases['supported']['scenarios']['3']['scenarios'][0]['result']
    assert capital['stock_reference_capital']=='6000' and capital['stock_share_count']==300
    missing=cases['missing']['scenarios']['3']['scenarios'][0]['result']
    assert missing['stock_reference_capital'] is None and missing['stock_share_count']==300
    target=os.environ.get('ALPHA_LEAPS_BROWSER_FIXTURE')
    if target:
        path=Path(target)
        assert not path.exists()
        write=json.dumps(data,indent=2,allow_nan=False)
        path.write_text(write,encoding='utf-8')  # generated QA artifact, not source
