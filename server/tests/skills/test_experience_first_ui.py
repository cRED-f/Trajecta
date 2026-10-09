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
    assert 'skills.learning.stop()' not in code


def test_retired_mining_coordinator_is_not_attached_to_chat_or_skills():
    assert not (ROOT / "server/src/skills/learning/coordinator.py").exists()
    for relative_path in (
        "server/src/chat/service.py",
        "server/src/skills/service.py",
    ):
        code = (ROOT / relative_path).read_text()
        assert "SkillLearningCoordinator" not in code
        assert "_skill_learning" not in code
    assert "SkillMiner(" not in (
        ROOT / "server/src/skills/service.py"
    ).read_text()
    assert (ROOT / "server/src/skills/skill_miner/miner.py").exists()
    assert "class SkillLearningConfig" not in (
        ROOT / "server/src/config/__init__.py"
    ).read_text()


def test_stale_learning_routes_are_explicitly_retired():
    code = (ROOT / "server/src/api/routes/skills.py").read_text()
    assert '@router.get("/learning/status")' in code
    assert '@router.post("/learning/run")' in code
    assert 'async def retired_skill_learning()' in code
    assert 'status_code=410' in code
    assert '"learning": await service.learning.status()' not in code


def test_stale_local_learning_settings_cannot_restart_the_worker():
    from server.src.config import Settings

    settings = Settings.model_validate({"skills": {"learning": {"enabled": True}}})
    assert "learning" not in settings.skills.model_fields_set
    assert not hasattr(settings.skills, "learning")


def test_manual_evaluation_and_experience_learning_are_preserved():
    skills = (ROOT / "server/src/skills/service.py").read_text()
    app = (ROOT / "server/src/api/app.py").read_text()
    assert "self.evaluator = SkillEvaluator" in skills
    assert "async def upgrade_skill(" in skills
    assert "ExperienceLearningService" in app
    assert "app.state.experience_learning = experiences" in app


def test_default_old_experiment_and_mining_controls_disabled():
    code = (ROOT / "config/default.yaml").read_text()
    experiment = code.split('  experiments:', 1)[1].split('  regression:', 1)[0]
    assert '  learning:' not in code
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
