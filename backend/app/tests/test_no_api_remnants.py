from pathlib import Path

def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="ignore")


def test_no_use_api_in_model_or_schema():
    repo_root = Path(__file__).resolve().parents[3]
    analysis_run_text = _read_text(repo_root / "backend/app/models/analysis_run.py")
    schema_text = _read_text(repo_root / "backend/app/services/schemas.py")
    assert "use_api" not in analysis_run_text
    assert "use_api" not in schema_text


def test_no_use_api_in_api_or_ui():
    repo_root = Path(__file__).resolve().parents[3]
    analysis_text = _read_text(repo_root / "backend/app/api/v1/analysis.py")
    template_text = _read_text(repo_root / "backend/app/templates/index.html")
    assert "use_api" not in analysis_text
    assert "use_api" not in template_text
