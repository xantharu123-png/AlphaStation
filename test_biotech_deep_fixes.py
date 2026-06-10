#!/usr/bin/env python3
"""
Biotech-Scanner Deep-Fixes (Biotech-Audit 10.06.2026) — Regression-Tests.

Abgedeckt:
- K-1: Fehlschlag-Erkennung (Verb-Formen, Negationsfenster, Roman→Arabisch,
       title+description) — die 5 Kipp-Faelle der Audit-Matrix + Bleib-Faelle
- H-4: FORWARD-Katalysatoren (expected/anticipated/...) → kein Score, Flag
- H-1 (Edge): NEAR_BINARY_EVENT ab T-3 unabhaengig von MCap/halt_risk
- M-3: bpiq_score zaehlt nicht mehr doppelt (pipeline + Edge)
- M-5: Device-Clearance (fda clearance / 510(k)) auf tier4-Niveau
- N:   Fuzzy-Quartals-Daten, LATER-Bucket, UTC-Datum, Plural "safety concerns"

Session-unabhaengig: Pfade via __file__, relative Daten, keine echten HTTP-Calls.
"""
import datetime
import os
import sys

_DIR = os.path.dirname(os.path.abspath(__file__))
if _DIR not in sys.path:
    sys.path.insert(0, _DIR)

import modules.scanners as scanners
import modules.data_fetchers as df

TODAY_UTC = datetime.datetime.now(datetime.timezone.utc).date()
RECENT = (TODAY_UTC - datetime.timedelta(days=2)).isoformat()  # frisch → voller Score


# ────────────────────────── Helpers ──────────────────────────

class _Resp:
    status_code = 200

    def __init__(self, articles):
        self._a = articles

    def json(self):
        return {"results": self._a}


def _classify(monkeypatch, title, desc=""):
    article = {
        "title": title,
        "description": desc,
        "published_utc": RECENT + "T12:00:00Z",
        "insights": [{"ticker": "TEST", "sentiment": "neutral"}],
        "article_url": "http://x",
    }
    monkeypatch.setattr(scanners, "rate_limited_get", lambda *a, **k: _Resp([article]))
    return scanners._scan_biotech_news("k", "TEST", limit=5)


def _readout(days, label="Phase 3 PDUFA", bpiq=85, **kw):
    d = {"days_until": days, "full_label": label, "stage_label": label,
         "event_label": "Readout", "category": "IMMINENT", "phase_mult": 3.0,
         "drug_name": "DrugX", "catalyst_date_text": "2026-07-01", "bpiq_score": bpiq}
    d.update(kw)
    return d


def _edge(readouts, mcap=3000, shares=30, price=20.0, news=None):
    trial = {"pipeline_score": 16, "readout_score": 12, "catalyst_readouts": readouts,
             "phase_summary": {}, "trials": [], "total_active": 0}
    news_data = news or {"catalyst_score": 0, "news": [], "negative_flags": []}
    tech = {"technical_score": 10,
            "details": {"price": price, "pos_90d": 60, "range_10d%": 6,
                        "RVOL": 2.0, "rvol_up_day": True, "chart_health": 8}}
    details = {"market_cap_millions": mcap, "shares_millions": shares}
    return scanners._calculate_biotech_catalyst_edge(trial, news_data, tech, details)


# ────────────────────────── K-1: Kipp-Faelle (Audit-Matrix) ──────────────────────────

def test_k1_misses_endpoint_is_negative(monkeypatch):
    r = _classify(monkeypatch, "Acme misses primary endpoint, shares plunge")
    assert r["catalyst_score"] == 0, "Verfehlter Endpunkt darf NICHT positiv scoren"
    flags = [f["flag"] for f in r["negative_flags"]]
    assert "missed endpoint" in flags
    assert r["best_catalyst"] is None


def test_k1_did_not_meet_is_negative_roman_normalized(monkeypatch):
    r = _classify(monkeypatch, "Acme Phase III study did NOT meet primary endpoint")
    assert r["catalyst_score"] == 0, "did-NOT-Negation muss den Positiv-Score blocken"
    flags = [f["flag"] for f in r["negative_flags"]]
    assert "did not meet" in flags


def test_k1_failed_to_meet_and_does_not_achieve(monkeypatch):
    r = _classify(monkeypatch, "Acme failed to meet primary endpoint in pivotal trial")
    assert r["catalyst_score"] == 0
    assert "failed to meet" in [f["flag"] for f in r["negative_flags"]]

    r2 = _classify(monkeypatch, "Acme data does not achieve statistical significance")
    assert r2["catalyst_score"] == 0
    assert "did not meet" in [f["flag"] for f in r2["negative_flags"]]


def test_k1_offerings_are_negative(monkeypatch):
    for title, expected in [
        ("Acme prices $50M public offering of common stock", "public offering"),
        ("Acme announces underwritten public offering", "public offering"),
        ("Acme announces $40M registered direct offering", "registered direct offering"),
    ]:
        r = _classify(monkeypatch, title)
        assert r["catalyst_score"] == 0, title
        flags = [f["flag"] for f in r["negative_flags"]]
        assert expected in flags, f"{title} → {flags}"
        # Stem-Dedupe: ein Offering = EIN Flag, kein Doppel aus Liste+Pattern
        assert len([f for f in flags if "offering" in f]) == 1, flags


def test_k1_discontinuation_is_negative(monkeypatch):
    r = _classify(monkeypatch, "Acme announces discontinuation of development program")
    assert r["catalyst_score"] == 0
    assert any("discontinu" in f["flag"] for f in r["negative_flags"])

    r2 = _classify(monkeypatch, "Acme discontinued development of lead asset")
    assert r2["catalyst_score"] == 0
    assert any("discontinu" in f["flag"] for f in r2["negative_flags"])


# ────────────────────────── K-1: Bleib-Faelle ──────────────────────────

def test_k1_positive_cases_stay_correct(monkeypatch):
    # Echter Readout positiv → tier2 (22)
    r = _classify(monkeypatch, "Acme reports positive Phase 3 data, primary endpoint met")
    assert r["catalyst_score"] == 22 and not r["negative_flags"]
    # FDA Approval → tier1 (30)
    r = _classify(monkeypatch, "Acme Pharma announces FDA approval of drug X")
    assert r["catalyst_score"] == 30 and not r["negative_flags"]
    # PDUFA-Datum gesetzt → tier1 (30, kein Forward-Trigger durch "set for")
    r = _classify(monkeypatch, "Acme PDUFA date set for September 2026")
    assert r["catalyst_score"] == 30 and not r.get("forward_catalyst")
    # Eingetretene Topline-Results → tier2 (22)
    r = _classify(monkeypatch, "Acme announces topline results from Phase 2")
    assert r["catalyst_score"] == 22 and not r.get("forward_catalyst")
    # Initiation bleibt KEIN Readout (Roman-Normalisierung erzeugt kein bare "phase 3"-Match)
    r = _classify(monkeypatch, "Acme initiates Phase 3 trial of drug X")
    assert r["catalyst_score"] == 0 and not r["negative_flags"]


def test_k1_negative_cases_stay_correct(monkeypatch):
    # CRL bleibt negativ, Remission bleibt sauber
    r = _classify(monkeypatch, "Acme receives Complete Response Letter from FDA")
    assert "complete response letter" in [f["flag"] for f in r["negative_flags"]]
    r = _classify(monkeypatch, "Patient achieves complete response in trial")
    assert not r["negative_flags"] and r["catalyst_score"] == 0
    # Clinical Hold bleibt negativ
    r = _classify(monkeypatch, "FDA places clinical hold on Acme trial")
    assert "clinical hold" in [f["flag"] for f in r["negative_flags"]]


def test_k1_no_safety_concerns_negation_guard(monkeypatch):
    """Plural matcht jetzt ("safety concerns"), aber die Verneinung
    "no safety concerns seen" darf KEIN Negativ-Flag erzeugen."""
    r = _classify(monkeypatch, "Acme well tolerated, no safety concerns seen")
    assert r["negative_flags"] == [], r["negative_flags"]

    r2 = _classify(monkeypatch, "Acme reports safety concerns in Phase 2 study")
    assert "safety concern" in [f["flag"] for f in r2["negative_flags"]], \
        "Plural 'safety concerns' muss matchen (N-d)"


def test_k1_negative_check_covers_description(monkeypatch):
    """K-1b: Negativ-Pruefung laeuft auf title UND description."""
    r = _classify(monkeypatch, "Acme provides corporate update",
                  desc="The company announced it missed the primary endpoint in its pivotal study.")
    assert r["catalyst_score"] == 0
    assert "missed endpoint" in [f["flag"] for f in r["negative_flags"]]


# ────────────────────────── H-4: FORWARD ──────────────────────────

def test_h4_forward_expected_no_score_but_flag(monkeypatch):
    r = _classify(monkeypatch, "Acme topline results expected in Q3 2026")
    assert r["catalyst_score"] == 0, "Angekuendigtes Ergebnis darf nicht wie eingetreten scoren"
    assert r["forward_catalyst"] is True
    assert r["negative_flags"] == []


def test_h4_forward_variants_and_pleased_to_report(monkeypatch):
    for title in [
        "Acme anticipated to report Phase 2 data in H1",
        "Acme on track to report topline results this year",
        "Acme will announce interim analysis at ASCO",
    ]:
        r = _classify(monkeypatch, title)
        assert r["catalyst_score"] == 0, title
        assert r["forward_catalyst"] is True, title
    # "pleased to report" ist Ergebnis-Sprache — KEIN Forward
    r = _classify(monkeypatch, "Acme pleased to report positive results from Phase 2")
    assert r["forward_catalyst"] is False
    assert r["catalyst_score"] > 0


# ────────────────────────── H-1 (Edge): NEAR_BINARY_EVENT ──────────────────────────

def test_h1_near_binary_sequence_midcap():
    """T-60/T-7: Run-up (Modus unveraendert) → ab T-3 kippt der Modus,
    Score-Adjustment mindestens -10, halt_risk +18 — bei 3-Mrd-MCap."""
    e60 = _edge([_readout(60)])
    e7 = _edge([_readout(7)])
    for e in (e60, e7):
        assert e["trade_mode"] != "NEAR_BINARY_EVENT"
        assert e["near_binary_event"] is False
        assert e["score_adjustment"] >= 0

    for days in (3, 1, 0, -1):
        e = _edge([_readout(days)])
        assert e["trade_mode"] == "NEAR_BINARY_EVENT", f"T{days:+d} muss kippen"
        assert e["near_binary_event"] is True
        assert e["score_adjustment"] <= -10
        assert e["halt_risk"] >= 18
        assert "near_binary_event" in e["risk_flags"]

    # Deutlicher Score-Abfall T-7 → T-3
    assert (e7["bio_edge_score"] + e7["score_adjustment"]) - \
           (_edge([_readout(3)])["bio_edge_score"] + _edge([_readout(3)])["score_adjustment"]) >= 15


def test_h1_near_binary_microcap_keeps_micro_flags():
    e = _edge([_readout(2)], mcap=80, shares=8, price=1.5)
    assert e["trade_mode"] == "NEAR_BINARY_EVENT"
    assert e["score_adjustment"] <= -10
    for fl in ("microcap_binary_risk", "microfloat_halt_risk", "penny_biotech_volatility"):
        assert fl in e["risk_flags"], f"Micro-Cap-Flag {fl} muss erhalten bleiben"


def test_h1_overdue_beyond_one_day_stays_overdue():
    e = _edge([_readout(-5)])
    assert e["trade_mode"] != "NEAR_BINARY_EVENT"
    assert "overdue_catalyst" in e["risk_flags"]


# ────────────────────────── M-3: Keine Doppelzaehlung ──────────────────────────

def test_m3_bpiq_score_not_double_counted():
    """Der bpiq_score (treibt bereits readout_score→pipeline_score) darf die
    Edge-catalyst_power nicht mehr erhoehen: 90 vs. 10 → identische Power."""
    hi = _edge([_readout(24, bpiq=90)])
    lo = _edge([_readout(24, bpiq=10)])
    assert hi["catalyst_power"] == lo["catalyst_power"], \
        f"bpiq_score zaehlt noch doppelt: {hi['catalyst_power']} vs {lo['catalyst_power']}"
    assert "high_catalyst_quality" not in hi["positive_factors"]


# ────────────────────────── M-5: Device-Clearance ──────────────────────────

def test_m5_fda_clearance_is_tier4(monkeypatch):
    r = _classify(monkeypatch, "Acme receives FDA clearance for blood test")
    assert r["catalyst_score"] == 8, f"Device-Clearance muss tier4 (8) sein, ist {r['catalyst_score']}"
    assert r["best_catalyst"]["tier"] == "tier4"

    r2 = _classify(monkeypatch, "Acme granted 510(k) clearance for cardiac device")
    assert r2["catalyst_score"] == 8
    # Drug-Approval bleibt tier1
    r3 = _classify(monkeypatch, "Acme Pharma announces FDA approval of drug X")
    assert r3["catalyst_score"] == 30


# ────────────────────────── N: Datums-Fixes (data_fetchers) ──────────────────────────

def test_n_fuzzy_quarter_dates():
    p = df._parse_fuzzy_catalyst_date
    assert p("Q3 2026") == "2026-08-15"
    assert p("H2 2026") == "2026-10-01"
    assert p("Q1 2027") == "2027-02-15"
    assert p("H1 2026") == "2026-04-01"
    assert p("2026 Q4") == "2026-11-15"
    assert p("TBA") is None
    assert p("") is None
    assert p(None) is None


def test_n_cache_builder_fuzzy_and_utc(monkeypatch):
    """End-to-End durch _load_bpiq_catalyst_cache: Fuzzy-Quartal wird zur
    Zeitraums-Mitte (date_estimated=True) statt verworfen; exaktes Datum
    'heute' (UTC) ergibt days_until == 0 (nie -1 durch naive Lokalzeit)."""
    # Quartal dynamisch waehlen: Mitte liegt 30-360 Tage voraus → Kategorie
    # garantiert gesetzt (Test laeuft an jedem Kalendertag).
    _q_mids = {1: "02-15", 2: "05-15", 3: "08-15", 4: "11-15"}
    q_label = q_date = None
    for _y in (TODAY_UTC.year, TODAY_UTC.year + 1):
        for _q in (1, 2, 3, 4):
            _d = datetime.date.fromisoformat(f"{_y}-{_q_mids[_q]}")
            if 30 <= (_d - TODAY_UTC).days <= 360:
                q_label, q_date = f"Q{_q} {_y}", _d
                break
        if q_label:
            break
    assert q_label is not None

    drugs = [
        {"ticker": "FUZZ", "drug_name": "FuzzDrug", "catalyst_date": None,
         "catalyst_date_text": q_label,
         "stage_event": {"stage_label": "Phase 3", "event_label": "Topline", "label": "Phase 3 Topline", "score": 80}},
        {"ticker": "TODY", "drug_name": "TodayDrug", "catalyst_date": TODAY_UTC.isoformat(),
         "catalyst_date_text": TODAY_UTC.isoformat(),
         "stage_event": {"stage_label": "Phase 3", "event_label": "PDUFA", "label": "PDUFA", "score": 90}},
    ]

    class _R:
        status_code = 200

        def json(self):
            return {"results": drugs, "next": None}

    monkeypatch.setattr(df, "rate_limited_get", lambda *a, **k: _R())
    monkeypatch.setattr(df, "_get_config_value", lambda k: "fake-key" if k == "BPIQ_API_KEY" else None)
    monkeypatch.setattr(df, "_BPIQ_CATALYST_CACHE", {})
    monkeypatch.setattr(df, "_BPIQ_CACHE_TIMESTAMP", 0)

    cache = df._load_bpiq_catalyst_cache()

    fuzz = cache["FUZZ"][0]
    assert fuzz["catalyst_date"] == q_date.isoformat(), "Quartal → Quartalsmitte als Datum"
    assert fuzz["date_estimated"] is True
    assert fuzz["days_until"] == (q_date - TODAY_UTC).days
    assert fuzz["category"] != "", "Fuzzy-Readout darf nicht still aus der Wertung fallen"

    tody = cache["TODY"][0]
    assert tody["days_until"] == 0, f"Heute (UTC) muss T-0 sein, nicht {tody['days_until']}"
    assert tody["date_estimated"] is False
    assert tody["category"] == "IMMINENT"


def test_n_later_bucket_appears_in_readouts(monkeypatch):
    """LATER scored (0.5*pm) → erscheint jetzt auch in catalyst_readouts."""
    later = {
        "company_name": "Late Bio", "drug_name": "LateDrug", "stage_label": "Phase 2",
        "event_label": "Readout", "full_label": "Phase 2 readout",
        "catalyst_date": (TODAY_UTC + datetime.timedelta(days=200)).isoformat(),
        "catalyst_date_text": "H2", "days_until": 200, "category": "LATER",
        "phase_mult": 2.0, "bpiq_score": 70,
    }
    monkeypatch.setattr(df, "_load_bpiq_catalyst_cache", lambda: {"LATE": [later]})
    res = df._get_bpiq_catalysts("LATE")
    assert res["bpiq_available"] is True
    assert res["readout_score"] >= 1, "LATER muss weiterhin scoren"
    assert res["catalyst_readouts"], "LATER-Readout muss in catalyst_readouts erscheinen (N-b)"
    assert res["catalyst_readouts"][0]["category"] == "LATER"
    assert res["readout_label"], "LATER-only braucht ein Label"


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-v"]))
