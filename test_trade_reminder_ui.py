"""Reminder UI contracts tested through real rendered controls, not copy matching."""
from test_reminder_controls_behavior import run_ui


def test_sidebar_exposes_consistent_trigger_and_retest_reminders():
    run_ui("""
entry=components.Controls;const saved=[];
render({eligible:true,isStructure:false,condition:'retest',onCreate:(...args)=>saved.push(args)});
assert.match(text(result),/Rücktest bestätigt/);
assert.deepEqual(nodes(named('Laufzeit','select')).filter(n=>n.type==='option').map(n=>Number(n.props.value)),[1,3,6,12,24]);
select('Laufzeit','3');check('App',false);named('Reminder setzen','button').props.onClick();
assert.deepEqual(saved,[[3,'email']]);
""")


def test_sidebar_persists_and_can_cancel_active_reminders():
    run_ui("""
entry=components.Controls;const deleted=[];
const props={eligible:false,busy:true,onCancel:id=>deleted.push(id),activeReminders:[
 {id:'mine',condition:'retest',expires_at:1893456000,channel:'browser'}]};
render(props);const button=nodes(result).find(n=>n.type==='button'&&text(n)==='Löschen');
assert.equal(button.props.disabled,true);button.props.onClick();assert.deepEqual(deleted,[]);
assert.match(text(result),/Aktiv bis/);assert.ok(!text(result).includes('unbekannt'));
render({...props,busy:false});named('Löschen','button').props.onClick();assert.deepEqual(deleted,['mine']);
""")
