"""Offline tests for aggregation only; no scans, APIs, mail, or execution."""
import copy
import json
import math
from types import SimpleNamespace

import pytest

from modules.bi_diagnostics import create_bi_diagnostics, observe_bi_analysis
from modules.patterns import (
    BI_STOCK_CONTRACT_VERSION, BI_STOCK_INDICATORS, BI_STOCK_REQUIRED_GREEN,
    BreakoutAnalysisResult, analyze_breakout_imminent,
)


def _diagnostics(**overrides):
    values = dict(
        direction="long", indicator_specs=BI_STOCK_INDICATORS,
        required_green=BI_STOCK_REQUIRED_GREEN, contract_version=BI_STOCK_CONTRACT_VERSION,
        run_id="run-20260908-123", code_revision="fa9fba7fdddc",
        started_at="2026-09-08T18:30:00+00:00",
    )
    values.update(overrides)
    return create_bi_diagnostics(**values)


def _result(green=17, unavailable=(), hard=(), core=None):
    checks = [
        dict(id=i, key=key, available=i not in unavailable,
             passed=i <= green and i not in unavailable,
             reason="PRIVATE ticker=SECRET price=123 token=DO_NOT_EXPORT")
        for i, key, _, _ in BI_STOCK_INDICATORS
    ]
    green_count = sum(c["passed"] for c in checks)
    available_count = sum(c["available"] for c in checks)
    if core is None:
        core = green_count >= 17 and available_count == 20 and not hard
    return BreakoutAnalysisResult(
        (core, 100, 173, ["PRIVATE_DETAIL"], 85, "PRIVATE_GRADE", 3, 3),
        indicator_checks=checks, green_count=green_count, available_count=available_count,
        required_green=17, indicator_contract_ok=available_count == 20,
        hard_gate_failures=hard, contract_version=BI_STOCK_CONTRACT_VERSION,
    )


def _observe(d, result, **kwargs):
    args = dict(bar_count=50, payload_accepted=False)
    args.update(kwargs)
    return observe_bi_analysis(d, result, **args)


def _identities(d):
    schema_valid = d["evaluated"] - d["schema_invalid"]
    assert sum(d["bar_count_histogram"].values()) == d["evaluated"]
    assert sum(d["green_count_histogram"].values()) == schema_valid
    assert sum(d["available_count_histogram"].values()) == schema_valid
    for counts in d["factor_counts"].values():
        assert counts["evaluated"] == counts["green"] + counts["red"]
        assert counts["evaluated"] + counts["unavailable"] == schema_valid
    assert d["payload_accepted_count"] <= d["pre_hard_gate_qualified"]
    assert d["core_valid_count"] <= d["pre_hard_gate_qualified"]
    assert sum(d["first_hard_gate_counts"].values()) <= d["pre_hard_gate_qualified"]
    assert d["incomplete"] <= schema_valid
    assert d["below_required"] <= schema_valid


def test_initial_schema_has_only_complete_fixed_keys_and_zero_counters():
    d = _diagnostics()
    assert set(d) == {
        "schema_version", "scanner", "direction", "required_green", "run_id",
        "code_revision", "contract_version", "started_at", "evaluated", "schema_invalid",
        "below_required", "incomplete", "pre_hard_gate_qualified", "core_valid_count",
        "payload_accepted_count", "observation_errors", "green_count_histogram",
        "available_count_histogram", "bar_count_histogram", "factor_counts",
        "first_hard_gate_counts",
        "failed_pair_counts", "consolidation_days_histogram",
    }
    assert d["schema_version"] == 1
    assert d["scanner"] == "bi_long"
    assert set(d["green_count_histogram"]) == set(map(str, range(21)))
    assert set(d["available_count_histogram"]) == set(map(str, range(21)))
    assert set(d["bar_count_histogram"]) == set(map(str, range(36, 51))) | {"other"}
    assert list(d["factor_counts"]) == [spec[1] for spec in BI_STOCK_INDICATORS]
    assert set(d["first_hard_gate_counts"]) == {
        "last_bar_pump", "range_breakdown", "recent_bearish_pressure",
        "recent_bullish_pressure", "unknown",
    }
    assert json.loads(json.dumps(d)) == d
    _identities(d)


@pytest.mark.parametrize("green", range(17))
def test_normal_low_confluence_and_absent_payload_are_not_schema_errors(green):
    d = _diagnostics()
    _observe(d, _result(green), payload_accepted=False)
    assert d["schema_invalid"] == 0
    assert d["below_required"] == 1
    assert d["incomplete"] == 0
    assert d["green_count_histogram"][str(green)] == 1
    assert d["available_count_histogram"]["20"] == 1
    assert d["pre_hard_gate_qualified"] == 0
    _identities(d)


@pytest.mark.parametrize("green", [17, 18, 19, 20])
@pytest.mark.parametrize("payload", [False, True])
def test_core_confluence_and_payload_are_separate_counts(green, payload):
    d = _diagnostics()
    assert _observe(d, _result(green), payload_accepted=payload) is None
    assert d["pre_hard_gate_qualified"] == d["core_valid_count"] == 1
    assert d["payload_accepted_count"] == int(payload)
    assert d["schema_invalid"] == d["below_required"] == d["incomplete"] == 0
    _identities(d)


@pytest.mark.parametrize("green,below", [(5, 1), (19, 0)])
def test_unavailable_is_not_schema_invalid_and_does_not_become_red(green, below):
    d = _diagnostics()
    _observe(d, _result(green, unavailable={20}))
    assert d["incomplete"] == 1
    assert d["below_required"] == below
    assert d["schema_invalid"] == 0
    assert d["available_count_histogram"]["19"] == 1
    assert d["factor_counts"]["candle_body_compression"] == {
        "evaluated": 0, "green": 0, "red": 0, "unavailable": 1,
    }
    assert d["pre_hard_gate_qualified"] == 0
    _identities(d)


@pytest.mark.parametrize("hard", [
    "last_bar_pump", "range_breakdown", "recent_bearish_pressure",
    "recent_bullish_pressure", "PRIVATE_token=secret@example.com",
])
def test_only_first_hard_gate_after_prequalification_is_counted(hard):
    d = _diagnostics()
    _observe(d, _result(17, hard=(hard, "range_breakdown")))
    assert d["pre_hard_gate_qualified"] == 1
    assert d["core_valid_count"] == 0
    expected = hard if hard in d["first_hard_gate_counts"] else "unknown"
    assert d["first_hard_gate_counts"][expected] == 1
    assert sum(d["first_hard_gate_counts"].values()) == 1
    assert "PRIVATE" not in json.dumps(d)
    _identities(d)


@pytest.mark.parametrize("green,unavailable", [(16, ()), (19, (20,))])
def test_stray_hard_gate_on_unqualified_row_is_not_claimed_as_causal(green, unavailable):
    d = _diagnostics()
    _observe(d, _result(green, unavailable=unavailable, hard=("last_bar_pump",)))
    assert d["schema_invalid"] == 0
    assert sum(d["first_hard_gate_counts"].values()) == 0
    _identities(d)


def test_unexplained_core_rejection_does_not_invent_a_hard_gate():
    d = _diagnostics()
    _observe(d, _result(17, core=False))
    assert d["schema_invalid"] == 0
    assert d["pre_hard_gate_qualified"] == 1
    assert d["core_valid_count"] == 0
    assert sum(d["first_hard_gate_counts"].values()) == 0


def test_qualified_hard_gate_rejection_with_helper_payload_is_not_schema_invalid():
    d = _diagnostics()
    _observe(d, _result(17, hard=("range_breakdown",)), payload_accepted=True)
    assert d["schema_invalid"] == 0
    assert d["pre_hard_gate_qualified"] == d["payload_accepted_count"] == 1
    assert d["core_valid_count"] == 0
    assert d["first_hard_gate_counts"]["range_breakdown"] == 1
    _identities(d)


@pytest.mark.parametrize("attribute,value", [
    ("indicator_checks", None), ("indicator_checks", []),
    ("green_count", True), ("green_count", 17.0), ("green_count", -1),
    ("green_count", 21), ("green_count", 16),
    ("available_count", False), ("available_count", 19),
    ("required_green", True), ("required_green", 16),
    ("indicator_contract_ok", 1), ("indicator_contract_ok", False),
    ("contract_version", "PRIVATE_UNKNOWN_VERSION"),
    ("hard_gate_failures", "PRIVATE_NOT_A_LIST"),
    ("hard_gate_failures", ({"PRIVATE_KEY": "PRIVATE_VALUE"},)),
])
def test_malformed_result_attributes_are_one_schema_failure_without_partial_factor_counts(attribute, value):
    d = _diagnostics()
    result = _result()
    setattr(result, attribute, value)
    _observe(d, result)
    assert d["evaluated"] == d["schema_invalid"] == 1
    assert d["below_required"] == d["incomplete"] == 0
    assert sum(d["green_count_histogram"].values()) == 0
    assert "PRIVATE" not in json.dumps(d)
    _identities(d)


@pytest.mark.parametrize("field,value", [
    ("id", True), ("id", 2), ("id", "1"),
    ("key", "PRIVATE_SECRET"), ("key", None),
    ("available", 1), ("available", "true"),
    ("passed", 1), ("passed", "true"),
])
def test_malformed_factor_fields_are_not_exported(field, value):
    d = _diagnostics()
    result = _result()
    result.indicator_checks[0][field] = value
    _observe(d, result)
    assert d["schema_invalid"] == 1
    assert "PRIVATE" not in json.dumps(d)
    _identities(d)


def test_missing_duplicate_and_reordered_factors_are_invalid():
    for mutation in ("missing", "duplicate", "reordered"):
        d = _diagnostics()
        result = _result()
        checks = list(result.indicator_checks)
        if mutation == "missing":
            checks.pop()
        elif mutation == "duplicate":
            checks[-1] = dict(checks[0])
        else:
            checks[0], checks[1] = checks[1], checks[0]
        result.indicator_checks = tuple(checks)
        _observe(d, result)
        assert d["schema_invalid"] == 1
        _identities(d)


def test_unavailable_cannot_be_green_even_when_attribute_totals_match():
    d = _diagnostics()
    result = _result()
    result.indicator_checks[0]["available"] = False
    result.available_count = 19
    result.indicator_contract_ok = False
    _observe(d, result)
    assert d["schema_invalid"] == 1


@pytest.mark.parametrize("result,payload", [
    (_result(16, core=True), False),
    (_result(17, hard=("last_bar_pump",), core=True), False),
    (_result(16), True),
])
def test_core_and_payload_permission_inconsistencies_are_observed_as_invalid(result, payload):
    d = _diagnostics()
    _observe(d, result, payload_accepted=payload)
    assert d["schema_invalid"] == 1
    assert d["core_valid_count"] == d["payload_accepted_count"] == 0


@pytest.mark.parametrize("bad", [None, (), {}, [], SimpleNamespace(), (True,)*7, (True,)*9])
def test_missing_or_non_tuple_result_schema_is_invalid(bad):
    d = _diagnostics()
    _observe(d, bad)
    assert d["schema_invalid"] == 1
    _identities(d)


def test_exceptions_from_result_attributes_never_leak():
    class Broken(tuple):
        @property
        def indicator_checks(self):
            raise RuntimeError("PRIVATE_TOKEN")
    d = _diagnostics()
    _observe(d, Broken((True, 1, 173, [], 85, "S", 0, 0)))
    assert d["schema_invalid"] == 1
    assert "PRIVATE" not in json.dumps(d)


@pytest.mark.parametrize("bar_count", list(range(36, 51)) + [0, 35, 51, 200])
def test_bar_count_bins_cover_each_observation(bar_count):
    d = _diagnostics()
    _observe(d, _result(16), bar_count=bar_count)
    key = str(bar_count) if 36 <= bar_count <= 50 else "other"
    assert d["bar_count_histogram"][key] == 1
    _identities(d)


@pytest.mark.parametrize("bar_count", [True, -1, math.nan, math.inf, "50", None])
def test_invalid_bar_count_types_are_fail_closed_without_leak(bar_count):
    d = _diagnostics()
    _observe(d, _result(), bar_count=bar_count)
    assert d["schema_invalid"] == 1
    assert d["bar_count_histogram"]["other"] == 1
    _identities(d)


@pytest.mark.parametrize("payload", [None, 1, "PRIVATE", {}, []])
def test_payload_argument_is_boolean_not_an_untrusted_payload(payload):
    d = _diagnostics()
    _observe(d, _result(), payload_accepted=payload)
    assert d["schema_invalid"] == 1
    assert "PRIVATE" not in json.dumps(d)


def test_result_registry_and_run_metadata_are_not_mutated():
    registry = copy.deepcopy(BI_STOCK_INDICATORS)
    d = _diagnostics(indicator_specs=registry)
    result = _result()
    before = (copy.deepcopy(tuple(result)), copy.deepcopy(result.__dict__))
    metadata = {k: d[k] for k in ("run_id", "started_at", "code_revision", "contract_version")}
    _observe(d, result, payload_accepted=True)
    assert tuple(result) == before[0]
    assert result.__dict__ == before[1]
    assert registry == BI_STOCK_INDICATORS
    assert all(d[k] == value for k, value in metadata.items())
    assert "PRIVATE" not in json.dumps(d)


def test_constructor_takes_registry_identity_not_names_or_points_as_truth():
    specs = [(i, key, "PRIVATE_LABEL", {"PRIVATE_POINTS": 9}) for i, key, _, _ in BI_STOCK_INDICATORS]
    d = _diagnostics(indicator_specs=specs)
    assert "PRIVATE" not in json.dumps(d)
    assert _diagnostics(indicator_specs=[spec[:2] for spec in BI_STOCK_INDICATORS]) == d


@pytest.mark.parametrize("key,value", [
    ("direction", "LONG"), ("direction", None),
    ("required_green", True), ("required_green", 0), ("required_green", 21),
    ("indicator_specs", BI_STOCK_INDICATORS[:-1]),
    ("contract_version", "PRIVATE\nTEXT"), ("run_id", "PRIVATE\nTEXT"),
    ("code_revision", "SECRET_API_KEY"), ("code_revision", None),
    ("started_at", "2026-09-08T18:30:00"), ("started_at", "PRIVATE_BAD_TIME"),
    ("started_at", None),
])
def test_invalid_constructor_metadata_raises_without_echoing_private_input(key, value):
    with pytest.raises(ValueError) as exc:
        _diagnostics(**{key: value})
    assert "PRIVATE" not in str(exc.value)
    assert "SECRET" not in str(exc.value)


def test_registry_must_have_exact_ids_and_unique_safe_keys():
    for change in ("id", "duplicate_key", "bad_key", "width"):
        specs = list(BI_STOCK_INDICATORS)
        if change == "id":
            specs[0] = (True, "atr_squeeze")
        elif change == "duplicate_key":
            specs[1] = (2, "atr_squeeze")
        elif change == "bad_key":
            specs[0] = (1, "PRIVATE_TOKEN@example.com")
        else:
            specs[0] = (1, "atr_squeeze", 9)
        with pytest.raises(ValueError):
            _diagnostics(indicator_specs=specs)


def test_runs_are_independent_and_timezone_is_explicit():
    first = _diagnostics(started_at="2026-09-08T18:30:00Z")
    second = _diagnostics(direction="short", run_id="separate-run")
    _observe(first, _result(17), payload_accepted=True)
    assert first["started_at"].endswith("+00:00")
    assert second["evaluated"] == 0
    assert second["scanner"] == "bi_short"
    assert second["factor_counts"]["atr_squeeze"]["evaluated"] == 0
    _identities(first)
    _identities(second)


@pytest.mark.parametrize("revision", ["fa9fba7fdddc", "fa9fba7fdddc-dirty", "fa9fba7fdddc-tree-unknown", "unknown"])
def test_known_revision_metadata_suffixes_are_preserved(revision):
    assert _diagnostics(code_revision=revision)["code_revision"] == revision


def test_mixed_observations_satisfy_all_denominator_invariants():
    d = _diagnostics()
    for result, accepted in [
        (_result(17), True), (_result(16), False),
        (_result(19, unavailable={20}), False),
        (_result(17, hard=("range_breakdown",)), False),
        (_result(3, unavailable={20}), False), (None, False),
    ]:
        _observe(d, result, payload_accepted=accepted)
        _identities(d)
    assert d["evaluated"] == 6
    assert d["schema_invalid"] == 1
    assert d["incomplete"] == 2
    assert d["below_required"] == 2
    assert d["pre_hard_gate_qualified"] == 2


# Fixed SYNTHETIC 50-bar witnesses; not historical prices or win-rate evidence.
# Extracted from the deterministic bounded probe, not read from private output.
_SYNTHETIC_BARS = {
    "long": (
        (36.22888779341928, 36.58836538128462, 36.22888779341928, 36.49658296990395, 1158866),
        (36.49658296990395, 36.72588681084546, 36.0979136877674, 36.33514690739136, 992950),
        (36.33514690739136, 36.38732904990302, 35.989026367621335, 36.07074673825163, 894995),
        (37.406899181201986, 37.75957699313161, 37.30741294301592, 37.5262903346081, 783290),
        (36.19013789165774, 36.34523438765937, 36.132820239331664, 36.27777446677355, 970181),
        (36.27777446677355, 36.504326274471126, 36.14749437777887, 36.45120689664955, 1036729),
        (36.45120689664955, 36.7848760322973, 36.23377156291667, 36.66827585681501, 796085),
        (36.66827585681501, 37.198948429871905, 36.45222471160641, 36.632999631386035, 942496),
        (36.854174408155856, 36.90386633242513, 36.499911799785465, 36.64567934210051, 797997),
        (36.495349848010164, 36.83353138964439, 36.44964953091245, 36.77667693035534, 820083),
        (36.77667693035534, 36.85184703914103, 36.3816665260334, 36.51300602716327, 971660),
        (36.51300602716327, 36.58761597905967, 36.353095459154396, 36.489507790903794, 1145896),
        (36.489507790903794, 36.78844034291977, 36.44375312847795, 36.56018286675229, 1166919),
        (36.56018286675229, 37.09619499988805, 36.415483781804305, 36.857139105669404, 961705),
        (36.857139105669404, 37.22035697022007, 36.688577013523314, 37.16724114117336, 963968),
        (37.16724114117336, 37.352226581881006, 36.90330952341922, 37.08744476334438, 766444),
        (37.08744476334438, 37.39384428897751, 36.949401621250814, 37.26313794195836, 1006542),
        (37.26313794195836, 37.42372151945597, 36.74081279298783, 37.025024702092075, 1115430),
        (37.025024702092075, 37.22964901157603, 36.85077107365054, 36.90093208491998, 1001243),
        (35.97343705248215, 37.23667548525587, 35.97343705248215, 37.189475694744665, 995973),
        (37.20139858242705, 37.484689687190496, 37.147605635372045, 37.3633543920668, 805090),
        (39.98024992315154, 40.39953558393725, 39.85200643447954, 40.29184823563342, 551687),
        (40.00907360827319, 40.05376970562324, 39.54165746983901, 39.74978589784436, 788086),
        (40.23667828442191, 40.296429239608386, 39.884654998460945, 39.98508493522285, 616071),
        (40.247792003055174, 40.319242380268484, 39.91571364092131, 40.244729379204315, 915381),
        (40.319141728240524, 40.52603072729549, 40.221464432037884, 40.44514044640269, 590037),
        (40.268857401170926, 40.52603072729549, 40.08649011968094, 40.44514044640269, 3086833),
        (40.41840626240905, 40.519269225224164, 40.25548266916354, 40.44514044640269, 555875),
        (40.242705917164535, 40.52603072729549, 40.06732544386063, 40.44514044640269, 801775),
        (40.24625023452011, 40.414735878307376, 39.991016004130735, 40.0386769328562, 564108),
        (40.32182250474862, 40.524969183001396, 40.17644457133806, 40.44514044640269, 2785473),
        (40.22886639364153, 40.52603072729549, 40.12316916132892, 40.44514044640269, 2710133),
        (40.28054702160402, 40.497555213879835, 40.1816066549282, 40.44514044640269, 2634793),
        (40.25304443839082, 40.52603072729549, 40.18239683920605, 40.44514044640269, 2559453),
        (40.31866749955492, 40.425001325670664, 40.08399473295299, 40.137405653037014, 562208),
        (40.162061477450294, 40.437503926647224, 40.03753949008224, 40.35716584695405, 2408774),
        (40.36442759875276, 40.4378650089138, 40.15471782863125, 40.399734479171315, 385460),
        (40.44741434378224, 40.52064569517419, 40.40254233105543, 40.464299793638894, 1013642),
        (40.372696154354344, 40.52959905712323, 40.21029948965614, 40.317282314857756, 483871),
        (40.39668815804104, 40.44514044640269, 40.34254064013846, 40.44514044640269, 2107414),
        (40.07342927784711, 40.148078623549175, 39.91340703063148, 39.93700405355189, 497464),
        (40.397842603199685, 40.46801209235529, 40.159392072837065, 40.260210743335094, 2544354),
        (40.26888650589899, 40.41450722660769, 40.22877370976584, 40.41450722660769, 4058763),
        (40.251288323651295, 40.314479656135255, 40.13212100061319, 40.13212100061319, 287736),
        (40.428266541180186, 40.442619031420854, 40.273620512228675, 40.30274866877014, 317235),
        (40.4203628021475, 40.47779561585508, 40.327563789484685, 40.44514044640269, 1655374),
        (40.22197786330563, 40.44386706811119, 40.22197786330563, 40.35757906859663, 258846),
        (40.404241922757556, 40.41829707469727, 40.208852858591406, 40.401690235202956, 414040),
        (40.33087641149621, 40.417153324639614, 40.13767704989135, 40.13767704989135, 185028),
        (40.28057524013988, 40.365717020619876, 40.20589003934789, 40.30355823958259, 520313),
    ),
    "short": (
        (40.67069705556683, 41.09233823660462, 40.541132683283, 40.87738562833503, 777391),
        (40.87738562833503, 41.02034616768786, 40.6564234715232, 40.775013981544234, 784723),
        (40.775013981544234, 40.936229569354936, 40.34096673754201, 40.43278602219245, 908889),
        (40.43278602219245, 40.76135863774476, 40.28119599742464, 40.6932447619525, 745551),
        (40.6932447619525, 41.002943153080004, 40.621011758916254, 40.943768764353365, 1005784),
        (40.943768764353365, 41.170594828092725, 40.653789667761046, 40.89048288657055, 1167536),
        (40.89048288657055, 41.10385578091138, 40.76154339554661, 41.01369962854315, 986056),
        (41.01369962854315, 41.19559928930902, 40.686193144933206, 40.88623463606009, 835474),
        (40.88623463606009, 40.9284687617028, 40.74099664730397, 40.887503805492074, 735974),
        (40.887503805492074, 41.08267283272381, 40.825268812046076, 40.897918898149946, 828389),
        (40.897918898149946, 41.19236920850218, 40.82156688248986, 41.10606412262675, 817809),
        (41.10606412262675, 41.20800933680223, 40.91191222740732, 41.04479284963609, 1008791),
        (41.04479284963609, 41.41344834572546, 40.82348031428975, 41.18082501475961, 1048018),
        (41.18082501475961, 41.337040275967645, 41.03293507029082, 41.175235338734225, 743055),
        (41.175235338734225, 41.255949862270484, 41.03022243227027, 41.17970485010545, 762204),
        (41.17970485010545, 41.32354678812525, 40.824768723225326, 40.938005516534005, 1134652),
        (39.92772470895408, 40.74965795108849, 39.63862754310077, 40.624650350133756, 848389),
        (39.47259941960585, 40.2654737224649, 39.05833069163776, 40.14615471633332, 730911),
        (39.49078546554736, 39.58211252243281, 38.75613417922994, 38.83845726470054, 2229392),
        (38.97008925355776, 39.155293231012664, 38.75613417922994, 38.83845726470054, 680939),
        (39.212738714155456, 39.36289296302147, 38.75613417922994, 38.83845726470054, 2153522),
        (39.57248076109685, 40.263487843587754, 39.20543785365205, 40.15653939767623, 767784),
        (39.42039104158379, 39.68561458543488, 38.75613417922994, 38.83845726470054, 595757),
        (39.462183762419464, 40.43076484631844, 39.08142204148583, 40.00388489952125, 810663),
        (39.512532367196606, 39.74795838672607, 38.75613417922994, 38.905695240755854, 827853),
        (39.16596784084431, 39.582663711018895, 38.75613417922994, 38.83845726470054, 1963846),
        (39.66034609336056, 40.05766239342413, 38.75613417922994, 39.09900042954111, 499453),
        (39.43549849423515, 40.10932417770454, 39.148864134462166, 39.89726629357465, 635366),
        (39.087562655649215, 39.632523630225826, 38.79067017427289, 39.530343299963334, 619894),
        (39.57619414604442, 40.26995478457412, 39.37514385153266, 40.00038610709923, 567445),
        (39.32144871227024, 39.5085297873476, 38.75613417922994, 38.8477800044484, 1774170),
        (39.0550767711419, 39.24464187358242, 38.75613417922994, 38.83845726470054, 1736234),
        (39.061350348563984, 39.73178143372089, 38.88274048738782, 39.43216709043732, 507462),
        (39.2296922854538, 39.69555758317621, 38.96973382295885, 39.58351423113213, 590289),
        (39.07166836292675, 39.467707435129995, 38.872743274929704, 39.408893984176146, 527174),
        (39.06043865770862, 39.43863877446757, 38.83227996324342, 39.381466426295155, 662905),
        (39.19668377575247, 39.57322519093951, 38.965792799997566, 39.50191216344222, 608509),
        (39.38785542708293, 39.76298771607775, 39.226280966083024, 39.67551263834582, 590217),
        (38.97091729506979, 39.12043945404943, 38.758424481616075, 38.84074756708667, 1470688),
        (38.828359584852315, 38.96603899651671, 38.78446249467191, 38.83845726470054, 519760),
        (39.18421915014809, 39.51138479689402, 39.08829100988847, 39.51138479689402, 463659),
        (39.124639175160425, 39.51169302913822, 39.05904805337928, 39.35684773485739, 525603),
        (38.987296146257215, 39.26223589910129, 38.82479715713787, 39.206096155653846, 350815),
        (39.04510729463625, 39.15588012812414, 38.80273210008391, 38.83845726470054, 391710),
        (39.393100837338906, 39.393100837338906, 38.759250013004284, 38.87192477414834, 408580),
        (38.91756779420876, 38.98442469651434, 38.75613417922994, 38.83845726470054, 469522),
        (39.0821524396816, 39.35091169598819, 39.04347912664872, 39.251302965537654, 362530),
        (38.934342059101596, 39.4387312265149, 38.83026797104375, 39.08041781841502, 315871),
        (39.055638733816295, 39.091043024141406, 38.76633756833388, 38.88446935002038, 336427),
        (38.8319178322116, 39.04338127604928, 38.782629846625596, 38.8468115683555, 1053401),
    ),
}


@pytest.mark.parametrize("direction,expected_score", [("long", 107), ("short", 113)])
def test_real_unmocked_analysis_reaches_seventeen_on_fixed_synthetic_ohlcv(direction, expected_score):
    bars = [dict(zip(("open", "high", "low", "close", "volume"), row))
            for row in _SYNTHETIC_BARS[direction]]
    assert len(bars) == 50
    assert all(0 < b["low"] <= min(b["open"], b["close"])
               <= max(b["open"], b["close"]) <= b["high"] and b["volume"] > 0 for b in bars)
    before = copy.deepcopy(bars)
    result = analyze_breakout_imminent(bars, direction=direction)
    assert result[0] is True
    assert result.green_count == 17
    assert result.available_count == 20
    assert result[1] == expected_score
    assert result.indicator_contract_ok is True
    assert not result.hard_gate_failures
    d = _diagnostics(direction=direction)
    assert _observe(d, result, payload_accepted=True) is None
    assert d["core_valid_count"] == d["payload_accepted_count"] == 1
    assert d["green_count_histogram"]["17"] == 1
    assert d["schema_invalid"] == 0
    assert bars == before
    _identities(d)
