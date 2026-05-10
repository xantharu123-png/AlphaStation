#!/usr/bin/env python3
"""
Biotech-Scanner Audit V3 — Regression Tests.

Testet die isolierten Logik-Stuecke der Biotech-Audit-V3 Fixes:
 1. BIOTECH_NEGATIVE_CATALYSTS semantisch korrekt ("complete response" alleine
    ist KEINE Ablehnung; nur "complete response letter" / "crl issued" sind)
 2. Grade-Thresholds (C=45+signal, B=62+, A=75+) — simulierte Logik
 3. Chart-Health-Penalty (<=4 => -15, <=6 => -8) — simulierte Logik
 4. min_required (35 mit Catalyst, 45 ohne) — simulierte Logik

Die Biotech-Main-Loop macht HTTP-Requests; wir testen deshalb die reinen
Logik-Bausteine ohne Netzwerk.
"""
import sys
import os
_dir = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _dir)

from modules.scanners import BIOTECH_NEGATIVE_CATALYSTS


# ── Test 1: Negative-Catalysts Dictionary ──

def test_negative_catalysts_semantic():
    failures = []
    # "complete response" OHNE "letter" darf NICHT als Negativ klassifiziert sein
    if "complete response" in BIOTECH_NEGATIVE_CATALYSTS:
        failures.append(
            f"BUG: 'complete response' ist in NEGATIVE_CATALYSTS ({BIOTECH_NEGATIVE_CATALYSTS['complete response']}) "
            f"— das ist aber in Trial-Kontext eine POSITIVE Remission, nicht FDA-Ablehnung."
        )
    # "complete response letter" MUSS als negativ drin sein
    if "complete response letter" not in BIOTECH_NEGATIVE_CATALYSTS:
        failures.append("'complete response letter' fehlt in NEGATIVE_CATALYSTS.")
    # "crl issued" sollte auch drin sein
    if "crl issued" not in BIOTECH_NEGATIVE_CATALYSTS:
        failures.append("'crl issued' fehlt in NEGATIVE_CATALYSTS.")
    # Standard-Negatives muessen drin bleiben
    for expected in ("clinical hold", "fda rejection", "trial failure", "missed endpoint"):
        if expected not in BIOTECH_NEGATIVE_CATALYSTS:
            failures.append(f"'{expected}' fehlt in NEGATIVE_CATALYSTS.")
    assert failures == []


# ── Test 2: Grade-Threshold-Logik ──
# Spiegelt die Logik aus scanners.py (main loop). Wird synchron gehalten.

def _apply_grade_logic(total_score, catalyst_score, has_readout, technical_score):
    has_cat_signal = catalyst_score > 0 or has_readout
    has_tech_signal = technical_score >= 8
    if total_score >= 75:
        return "A"
    elif total_score >= 62:
        return "B"
    elif total_score >= 45 and (has_cat_signal or has_tech_signal):
        return "C"
    else:
        return "D"


def test_grade_thresholds():
    failures = []
    cases = [
        # (name, total, cat, readout, tech, expected_grade)
        ("A-Grade bei 75", 75, 30, False, 12, "A"),
        ("A-Grade bei 100", 100, 45, False, 20, "A"),
        ("B-Grade bei 62", 62, 20, False, 10, "B"),
        ("B-Grade bei 74", 74, 30, False, 12, "B"),
        # Grenzfall: B war frueher bei 55 ausgeloest -> jetzt D
        ("55 waere frueher B, jetzt D ohne Signal", 55, 0, False, 2, "D"),
        ("55 mit Catalyst -> C (>=45)", 55, 10, False, 5, "C"),
        # Grade C braucht Signal
        ("C bei 45 mit Catalyst", 45, 10, False, 2, "C"),
        ("C bei 50 mit Tech-Signal (tech>=8)", 50, 0, False, 10, "C"),
        ("D bei 50 ohne jedes Signal", 50, 0, False, 2, "D"),
        # Alter C-Threshold 35 ist jetzt D
        ("35 ist jetzt D (war frueher C)", 35, 10, False, 5, "D"),
        ("44 mit Catalyst = D (unter 45)", 44, 20, False, 5, "D"),
        # Readout als Signal
        ("C bei 46 mit Readout", 46, 0, True, 3, "C"),
    ]
    for name, total, cat, readout, tech, expected in cases:
        actual = _apply_grade_logic(total, cat, readout, tech)
        if actual != expected:
            failures.append(f"{name}: erwartet {expected}, bekam {actual}")
    assert failures == []


# ── Test 3: Chart-Health-Penalty ──

def _apply_chart_health_penalty(total_score, chart_health):
    if chart_health <= 4:
        return max(0, total_score - 15)
    elif chart_health <= 6:
        return max(0, total_score - 8)
    return total_score


def test_chart_health_penalty():
    failures = []
    cases = [
        ("Perfekter Chart 10 -> kein Abzug", 70, 10, 70),
        ("Guter Chart 8 -> kein Abzug", 70, 8, 70),
        ("Schwach 6 -> -8", 70, 6, 62),
        ("Schwach 5 -> -8", 70, 5, 62),
        ("Kritisch 4 -> -15", 70, 4, 55),
        ("Kritisch 2 -> -15", 70, 2, 55),
        ("Floor nicht unter 0", 10, 2, 0),
    ]
    for name, total, health, expected in cases:
        actual = _apply_chart_health_penalty(total, health)
        if actual != expected:
            failures.append(f"{name}: erwartet {expected}, bekam {actual}")
    assert failures == []


# ── Test 4: min_required-Threshold ──

def _apply_min_required(total_score, catalyst_score, has_readout):
    if catalyst_score > 0 or has_readout:
        min_required = 35
    else:
        min_required = 45
    return total_score >= min_required


def test_min_required():
    failures = []
    cases = [
        # (name, total, cat, readout, should_pass)
        ("Mit Catalyst 35 -> Pass", 35, 10, False, True),
        ("Mit Catalyst 34 -> Reject", 34, 10, False, False),
        ("Ohne Catalyst 44 -> Reject", 44, 0, False, False),
        ("Ohne Catalyst 45 -> Pass", 45, 0, False, True),
        ("Mit Readout 35 -> Pass", 35, 0, True, True),
        # Alter Threshold 20 mit Catalyst war zu locker
        ("Mit Catalyst 20 -> REJECT (war frueher Pass)", 20, 10, False, False),
        ("Ohne Catalyst 35 -> REJECT (war frueher Pass)", 35, 0, False, False),
    ]
    for name, total, cat, readout, expected_pass in cases:
        actual = _apply_min_required(total, cat, readout)
        if actual != expected_pass:
            failures.append(f"{name}: erwartet pass={expected_pass}, bekam pass={actual}")
    assert failures == []


# ── Test 5: Kombinierter End-to-End-Flow ──
# Simuliert den Gesamtfluss: total_score -> chart_penalty -> min_required -> grade

def _full_pipeline(base_score, chart_health, catalyst_score, has_readout, technical_score):
    after_penalty = _apply_chart_health_penalty(base_score, chart_health)
    if not _apply_min_required(after_penalty, catalyst_score, has_readout):
        return None, after_penalty  # filtered out
    grade = _apply_grade_logic(after_penalty, catalyst_score, has_readout, technical_score)
    return grade, after_penalty


def test_full_pipeline():
    failures = []
    cases = [
        # (name, base, health, cat, readout, tech, expected_grade_or_None, expected_final_score)
        ("Guter Biotech: base 70 + perfekter chart + Catalyst + Tech", 70, 9, 20, False, 10, "B", 70),
        ("Kaputter Chart schiebt 70->55, immer noch C (Catalyst-Signal)", 70, 4, 20, False, 5, "C", 55),
        ("Kaputter Chart + schwacher Score filtert raus: 50->35 ohne Catalyst", 50, 4, 0, False, 5, None, 35),
        ("Schwacher Chart (-8), aber B bleibt C (62 -> 54)", 62, 6, 15, False, 7, "C", 54),
        ("Hoher Score 80 mit miesem Chart 4 bleibt B (80->65)", 80, 4, 30, False, 15, "B", 65),
        ("D nach Chart-Penalty (40 ohne Signal)", 40, 10, 0, False, 3, None, 40),
    ]
    for case in cases:
        name, base, health, cat, readout, tech, exp_grade, exp_score = case
        act_grade, act_score = _full_pipeline(base, health, cat, readout, tech)
        if act_grade != exp_grade:
            failures.append(f"{name}: erwartetes Grade={exp_grade}, bekam {act_grade} (score={act_score})")
        if act_score != exp_score:
            failures.append(f"{name}: erwarteter Score={exp_score}, bekam {act_score}")
    assert failures == []


# ── Runner ──

def main():
    print("TEST: Biotech-Scanner Audit V3 Regression")
    suites = [
        ("1. NEGATIVE_CATALYSTS Semantik", test_negative_catalysts_semantic),
        ("2. Grade-Thresholds", test_grade_thresholds),
        ("3. Chart-Health-Penalty", test_chart_health_penalty),
        ("4. min_required", test_min_required),
        ("5. End-to-End-Pipeline", test_full_pipeline),
    ]
    total_fail = 0
    total_pass = 0
    for name, fn in suites:
        fails = fn()
        if fails:
            print(f"\n[FAIL] {name}:")
            for f in fails:
                print(f"   - {f}")
            total_fail += len(fails)
        else:
            print(f"[PASS] {name}")
            total_pass += 1
    print("\n" + "=" * 60)
    if total_fail == 0:
        print(f"{len(suites)}/{len(suites)} Suiten PASS")
        return 0
    else:
        print(f"{total_pass}/{len(suites)} Suiten PASS, {total_fail} einzelne Failures")
        return 1


if __name__ == "__main__":
    sys.exit(main())
