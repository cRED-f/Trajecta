"""UI contract checks for the unified Memory + Skills navigation."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3] / "apps" / "desktop" / "src"


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_one_knowledge_entry_in_sidebar():
    sidebar = read("components/Sidebar.tsx")
    assert 'onNavigate("knowledge")' in sidebar
    assert 'onNavigate("memory")' not in sidebar
    assert 'onNavigate("skills")' not in sidebar
    assert 'onOpenMemory' not in sidebar
    assert 'Saved memory</span>' not in sidebar
    assert 'sidebar-nav-icon' in sidebar


def test_four_primary_sections_and_legacy_navigation():
    hub = read("components/KnowledgeCenter.tsx")
    assert all(f'id: "{view}"' in hub for view in ("overview", "memories", "skills", "review"))
    page = read("components/TopLevelPages.tsx")
    assert '<KnowledgeCenter backendOnline=' in page
    assert 'page === "skills" ? "skills"' in page  # legacy destination stays functional


def test_memory_editing_is_preserved_after_removing_duplicate_panel():
    memory = read("components/settings/MemorySettings.tsx")
    assert 'actions.addMemory({ key:' in memory
    assert 'actions.deleteMemory(memory.key)' in memory
    assert 'editFact(memory)' in memory
    assert not (ROOT / "components/MemoryPanel.tsx").exists()


def test_proposed_workflows_and_skill_approvals_still_separate():
    hub = read("components/KnowledgeCenter.tsx")
    procedures = read("components/memory/ProceduralMemories.tsx")
    experience = read("components/skills/ExperiencePanel.tsx")
    assert 'reviewOnly />' in hub
    assert 'draft.status === "needs_review"' in procedures
    assert 'I have reviewed the complete instructions' in experience
    assert 'setSkillEnabled' in experience
    assert 'VersionHistory' in experience


def test_diagnostics_available_without_extra_sidebar_destination():
    hub = read("components/KnowledgeCenter.tsx")
    assert 'view="activity"' in hub
    assert 'view="maintenance"' in hub
    assert 'knowledge-diagnostics' in hub
