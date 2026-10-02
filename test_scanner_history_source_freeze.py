"""A replay must fingerprint the code it loaded, not a later filesystem state."""
import ast
from pathlib import Path

import pytest

from scripts import scanner_history_runtime as runtime
from scripts import scanner_history_bi as bi
from scripts import scanner_history_sector as sector
from scripts import scanner_history_special_stocks as special
from scripts import scanner_history_stock as stock
from scripts import scanner_history_crypto as crypto


ADAPTERS = (bi, sector, special, stock, crypto)


def _observed_source_change(monkeypatch, relative_path):
    """Simulate an observed code replacement without editing actual source."""
    target = (runtime.ROOT / relative_path).resolve()
    assert target.is_file(), "The source-control counterexample must target a real file."
    original_read = Path.read_bytes

    def replaced(path):
        original = original_read(path)
        return original + b"\n# source replaced after the producer was imported\n" if path.resolve() == target else original

    monkeypatch.setattr(Path, "read_bytes", replaced)


def test_research_runtime_fingerprints_all_adapters_and_nested_production_sources():
    fingerprints = runtime.scanner_source_fingerprints()
    expected = {path.relative_to(runtime.ROOT).as_posix()
                for path in (runtime.ROOT / "modules").rglob("*.py")}
    expected.update(path.relative_to(runtime.ROOT).as_posix()
                    for path in (runtime.ROOT / "scripts").glob("scanner_history_*.py"))
    assert expected <= fingerprints.keys()
    assert {"api.py", "bg_service.py", "scripts/scanner_history_runtime.py"} <= fingerprints.keys()


@pytest.mark.parametrize("relative_path", [
    "api.py", "modules/patterns.py", "modules/crypto_scan_runtime.py",
    "scripts/scanner_history_bi.py", "scripts/scanner_history_crypto.py",
    "scripts/scanner_history_runtime.py",
])
def test_import_baseline_cannot_be_replaced_by_new_hashes(monkeypatch, relative_path):
    before = dict(runtime._IMPORT_SOURCE_FINGERPRINTS)
    _observed_source_change(monkeypatch, relative_path)
    assert runtime.scanner_source_fingerprints() != before
    with pytest.raises(ValueError, match="scanner_calculation_sources_changed_during_replay"):
        runtime.imported_scanner_source_fingerprints()
    with pytest.raises(ValueError, match="scanner_calculation_sources_changed_during_replay"):
        runtime.require_unchanged_scanner_sources(before)


@pytest.mark.parametrize("adapter", ADAPTERS, ids=lambda module: module.__name__.split("_")[-1])
def test_every_replay_rejects_source_changed_after_import_before_reading_data(monkeypatch, tmp_path, adapter):
    # The old analyzer alias really remains loaded. A fresh build baseline must
    # not silently attach changed on-disk source hashes to this old function.
    loaded_analyzer = bi.analyze_breakout_imminent
    _observed_source_change(monkeypatch, "modules/patterns.py")
    with pytest.raises(ValueError, match="scanner_calculation_sources_changed_during_replay"):
        if adapter in (bi, sector):
            adapter.build_report(tmp_path)
        elif adapter is crypto:
            adapter.study_directory(object(), tmp_path)
        else:
            adapter.replay(tmp_path, tmp_path / "not-created.json")
    assert bi.analyze_breakout_imminent is loaded_analyzer
    assert not (tmp_path / "not-created.json").exists()


@pytest.mark.parametrize("adapter", (bi, sector, special))
def test_adapter_import_snapshot_precedes_its_first_production_dependency(adapter):
    tree = ast.parse(Path(adapter.__file__).read_text(encoding="utf-8"))
    baseline_line = next(node.lineno for node in tree.body
                         if isinstance(node, ast.Assign)
                         and any(isinstance(target, ast.Name) and target.id == "_IMPORT_SOURCE_FINGERPRINTS"
                                 for target in node.targets))
    imports = [node.lineno for node in tree.body if isinstance(node, ast.ImportFrom)
               and (str(node.module).startswith("modules") or node.module == "scripts.scanner_history_bi")]
    assert imports and baseline_line < min(imports)


def test_unchanged_import_baseline_is_a_copy_not_mutable_global_state():
    snapshot = runtime.imported_scanner_source_fingerprints()
    assert all(adapter._IMPORT_SOURCE_FINGERPRINTS == snapshot for adapter in ADAPTERS)
    snapshot["api.py"] = "not-a-source-hash"
    assert runtime._IMPORT_SOURCE_FINGERPRINTS["api.py"] != "not-a-source-hash"
    runtime.require_unchanged_scanner_sources(runtime._IMPORT_SOURCE_FINGERPRINTS)


def test_crypto_directory_output_carries_all_source_provenance(monkeypatch, tmp_path):
    monkeypatch.setattr(crypto, "load_spot_source", lambda *_a: ([], {"source_bars_sha256": "fixed"}))
    monkeypatch.setattr(crypto, "study_asset", lambda *_a: {"contract": _a[1], "families": {}})
    report = crypto.study_directory(object(), tmp_path)
    assert report["scanner_source_sha256"] == crypto._IMPORT_SOURCE_FINGERPRINTS
    assert report["api_source_sha256"] == report["scanner_source_sha256"]["api.py"]
    assert report["full_scanner_replay_available"] is False
