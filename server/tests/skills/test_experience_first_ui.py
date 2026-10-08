"""Guard against bringing legacy evaluation-first UI and auto-mining back."""
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

ROOT = Path(__file__).resolve().parents[3]


def test_skills_screen_does_not_mount_old_dashboard():
    code = (ROOT / "apps/desktop/src/components/settings/SkillsSettings.tsx").read_text()
    assert '<ExperiencePanel enabled=' in code
    assert '<LearningStatus' not in code
    assert 'useSkillExperiments(' not in code
    assert 'SkillEvaluationWorkbench' not in code


def test_startup_does_not_boot_background_mining_worker():
    code = (ROOT / "server/src/api/app.py").read_text()
    assert 'skills.learning.start()' not in code


def test_default_old_experiment_and_mining_controls_disabled():
    code = (ROOT / "config/default.yaml").read_text()
    learning = code.split('  learning:', 1)[1].split('  experiments:', 1)[0]
    experiment = code.split('  experiments:', 1)[1].split('  regression:', 1)[0]
    assert 'enabled: false' in learning
    assert 'auto_evaluate: false' in learning
    assert 'enabled: false' in experiment


@pytest.mark.asyncio
async def test_learning_overview_does_not_call_evaluation_or_experiments():
    """Run the real standalone route module without importing Deep Agents."""
    import importlib.util
    path = ROOT / "server/src/api/routes/learning.py"
    spec = importlib.util.spec_from_file_location("experience_first_route_test", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    learner = SimpleNamespace(list=AsyncMock(return_value=[{"id": "experience"}]))
    repository = SimpleNamespace(
        list_registered=AsyncMock(return_value=[{"name": "my-skill"}]),
        list_candidates=AsyncMock(return_value=[]),
    )
    skills = SimpleNamespace(repository=repository, evaluator=AsyncMock(), experiments=AsyncMock())
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(
        experience_learning=learner, skills_service=skills,
    )))
    result = await module.learning_overview(request)
    assert result == {
        "items": [{"id": "experience"}],
        "skills": [{"name": "my-skill"}],
        "previous_candidates": [],
    }
    skills.evaluator.assert_not_called()
    skills.experiments.assert_not_called()
