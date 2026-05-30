from pathlib import Path


ROOT = Path(__file__).resolve().parent


def test_early_movers_refreshes_when_scheduler_cache_changes():
    source = (ROOT / "frontend" / "index.html").read_text(encoding="utf-8")

    assert "const schedulerLastRun = schedulerStatus?.scans?.early_movers?.last_run || null;" in source
    assert "loadResults({ force: false });" in source
    assert "updateScanCache('em_cached_at', nextCachedAt);" in source
    assert "lastLoadedCachedAtRef.current === nextCachedAt" in source

