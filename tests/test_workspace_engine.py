"""Tests for app.workspace.engine and app.workspace.templates."""

from pathlib import Path

import pytest

from app.workspace.engine import WorkspaceEngine
from app.workspace.templates import TEMPLATE_REGISTRY, list_templates, scaffold


@pytest.fixture()
def projects_dir(tmp_path: Path) -> Path:
    d = tmp_path / "projects"
    d.mkdir()
    return d


@pytest.fixture()
def engine(projects_dir: Path, tmp_path: Path) -> WorkspaceEngine:
    return WorkspaceEngine(str(projects_dir), localforge_root=tmp_path / "localforge")


# --- WorkspaceEngine ---


def test_create_workspace(engine: WorkspaceEngine, projects_dir: Path):
    path = engine.create_workspace("my-project")
    assert path.exists()
    assert path.name == "my-project"
    assert path.parent == projects_dir


def test_create_workspace_git_init(engine: WorkspaceEngine):
    path = engine.create_workspace("git-test")
    # git init creates .git directory
    assert (path / ".git").exists()


def test_create_workspace_duplicate(engine: WorkspaceEngine):
    engine.create_workspace("dup")
    with pytest.raises(FileExistsError):
        engine.create_workspace("dup")


def test_create_workspace_invalid_name(engine: WorkspaceEngine):
    with pytest.raises(ValueError):
        engine.create_workspace("../evil")
    with pytest.raises(ValueError):
        engine.create_workspace("")
    with pytest.raises(ValueError):
        engine.create_workspace(".hidden")


def test_set_active(engine: WorkspaceEngine):
    engine.create_workspace("ws1")
    path = engine.set_active("phone1", "ws1")
    assert engine.get_active_root("phone1") == path


def test_set_active_nonexistent(engine: WorkspaceEngine):
    with pytest.raises(FileNotFoundError):
        engine.set_active("phone1", "nonexistent")


def test_get_active_root_default(engine: WorkspaceEngine):
    root = engine.get_active_root("unknown_phone")
    assert root == engine.localforge_root


def test_list_workspaces(engine: WorkspaceEngine, projects_dir: Path):
    (projects_dir / "proj-a").mkdir()
    (projects_dir / "proj-b").mkdir()
    (projects_dir / ".hidden").mkdir()

    ws = engine.list_workspaces()
    names = [w["name"] for w in ws]
    assert "proj-a" in names
    assert "proj-b" in names
    assert ".hidden" not in names


def test_list_workspaces_empty(engine: WorkspaceEngine):
    assert engine.list_workspaces() == []


def test_is_valid_path(engine: WorkspaceEngine):
    ws = engine.create_workspace("valid-ws")
    engine.set_active("p1", "valid-ws")

    assert engine.is_valid_path(ws / "some_file.py", "p1") is True
    assert engine.is_valid_path(Path("/etc/passwd"), "p1") is False


def test_no_projects_root():
    engine = WorkspaceEngine("")
    assert engine.projects_root is None
    with pytest.raises(RuntimeError):
        engine.create_workspace("test")


# --- Templates ---


def test_list_templates():
    templates = list_templates()
    names = [t["name"] for t in templates]
    assert "html-static" in names
    assert "python-fastapi" in names
    assert "react-vite" in names
    assert "nextjs" in names


def test_scaffold_html_static(engine: WorkspaceEngine):
    path, files = scaffold(engine, "my-site", "html-static", description="A cafe site")
    assert path.exists()
    assert "index.html" in files
    assert "styles.css" in files
    # Check placeholder replacement
    html = (path / "index.html").read_text()
    assert "my-site" in html
    assert "A cafe site" in html


def test_scaffold_python_fastapi(engine: WorkspaceEngine):
    path, files = scaffold(engine, "my-api", "python-fastapi")
    assert (path / "main.py").exists()
    assert (path / "requirements.txt").exists()
    assert (path / "tests" / "test_api.py").exists()


def test_scaffold_react_vite(engine: WorkspaceEngine):
    path, files = scaffold(engine, "my-app", "react-vite")
    assert (path / "package.json").exists()
    assert (path / "src" / "App.tsx").exists()


def test_scaffold_nextjs(engine: WorkspaceEngine):
    path, files = scaffold(engine, "my-next", "nextjs")
    assert (path / "package.json").exists()
    assert (path / "app" / "page.tsx").exists()


def test_scaffold_unknown_template(engine: WorkspaceEngine):
    with pytest.raises(ValueError, match="Unknown template"):
        scaffold(engine, "test", "nonexistent-template")


def test_all_templates_produce_valid_files(engine: WorkspaceEngine):
    """Verify every template can be scaffolded without errors."""
    for i, name in enumerate(TEMPLATE_REGISTRY):
        path, files = scaffold(engine, f"test-{name}-{i}", name, description="Test project")
        assert len(files) > 0
        for f in files:
            assert (path / f).exists(), f"Missing file {f} in template {name}"
