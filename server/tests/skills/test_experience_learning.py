"""The synchronous preference and feedback learning contract."""
from pathlib import Path

ROOT=Path(__file__).resolve().parents[2] / "src"

def test_preference_and_correction_are_automatic():
    src=(ROOT/"skills/learning/experience.py").read_text()
    assert 'kind="preference", status="active"' in src
    assert 'kind="correction", status="active"' in src
    assert 'async def review(' not in src
    assert 'target_kind = kind' in src

def test_user_feedback_is_not_required_for_candidates():
    src=(ROOT/"memory/learning/skills.py").read_text()
    assert 'user_feedback' not in src
    assert 'outcome_verified=1' in src
    assert 'SkillRisk.READ_ONLY' in src
