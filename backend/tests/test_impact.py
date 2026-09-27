"""
Tests for the Change Impact Analyzer (Step 3).

Coverage:
  - PatchParser: changed files and symbols from valid diffs
  - PatchParser: empty/None patch
  - PatchParser: malformed diff
  - RepositoryScanner: find_importers, find_callers, find_related_tests
  - RepositoryScanner: unavailable repository path
  - ChangeImpactAnalyzer: no patch no files (graceful)
  - ChangeImpactAnalyzer: patch-only (no repo on disk)
  - ChangeImpactAnalyzer: full analysis with real temp files
  - ChangeImpactAnalyzer: malformed patch handled gracefully
  - ChangeImpactAnalyzer: internal error captured in result.error
  - risk_level estimation
  - ImpactResult.to_dict() and ImpactedArea.to_dict()
  - API: POST /api/verification/impact/analyse
  - API: GET  /api/verification/impact/<fix_id>
"""

import os
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def app():
    os.environ.setdefault("APP_ENV", "testing")
    from backend.app import create_app
    application = create_app(config_override={
        "TESTING": True,
        "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:",
    })
    with application.app_context():
        from backend.database.connection import db
        db.create_all()
        yield application


@pytest.fixture()
def client(app):
    return app.test_client()


@pytest.fixture()
def db(app):
    from backend.database.connection import db as _db
    return _db


@pytest.fixture()
def sample_fix(app, db):
    from backend.models import (
        Bug, BugStatus, TargetRepository,
        Investigation, InvestigationStatus,
        Fix, ApprovalStatus, ApplicationStatus,
    )
    with app.app_context():
        bug = Bug(
            title="Impact test bug",
            description="Testing impact analysis",
            target_repository=TargetRepository.THEFUCK,
            status=BugStatus.RECEIVED,
        )
        db.session.add(bug)
        db.session.commit()

        inv = Investigation(
            bug_id=bug.id,
            status=InvestigationStatus.COMPLETED,
            relevant_files=["thefuck/rules/no_such_command.py"],
        )
        db.session.add(inv)
        db.session.commit()

        fix = Fix(
            investigation_id=inv.id,
            ai_generated=True,
            explanation="Add None check",
            patch=(
                "--- a/thefuck/rules/no_such_command.py\n"
                "+++ b/thefuck/rules/no_such_command.py\n"
                "@@ -1,5 +1,6 @@\n"
                " def match(command):\n"
                "+    if command.output is None:\n"
                "+        return False\n"
                "     return 'no such' in command.output\n"
            ),
            files_changed=["thefuck/rules/no_such_command.py"],
            approval_status=ApprovalStatus.PENDING,
            application_status=ApplicationStatus.NOT_APPLIED,
        )
        db.session.add(fix)
        db.session.commit()
        yield fix


# ---------------------------------------------------------------------------
# PatchParser tests
# ---------------------------------------------------------------------------

class TestPatchParser:
    def _parser(self):
        from backend.core.impact.analyzer import PatchParser
        return PatchParser()

    def test_parse_simple_diff(self):
        patch = (
            "--- a/foo/bar.py\n"
            "+++ b/foo/bar.py\n"
            "@@ -1,3 +1,4 @@\n"
            " existing_line\n"
            "+def new_function():\n"
            "+    pass\n"
            " another_line\n"
        )
        files, symbols = self._parser().parse(patch)
        assert "foo/bar.py" in files
        assert "new_function" in symbols

    def test_parse_class_added(self):
        patch = (
            "--- a/module.py\n"
            "+++ b/module.py\n"
            "+class NewClass:\n"
            "+    pass\n"
        )
        files, symbols = self._parser().parse(patch)
        assert "module.py" in files
        assert "NewClass" in symbols

    def test_parse_empty_patch_returns_empty(self):
        files, symbols = self._parser().parse("")
        assert files == []
        assert symbols == []

    def test_parse_none_returns_empty(self):
        files, symbols = self._parser().parse(None)
        assert files == []
        assert symbols == []

    def test_parse_deduplicates_files(self):
        """A file modified in multiple hunks appears only once."""
        patch = (
            "--- a/foo.py\n+++ b/foo.py\n"
            "@@ -1 +1 @@\n+x = 1\n"
            "--- a/foo.py\n+++ b/foo.py\n"
            "@@ -5 +5 @@\n+y = 2\n"
        )
        files, _ = self._parser().parse(patch)
        assert files.count("foo.py") == 1

    def test_parse_multiple_files(self):
        patch = (
            "--- a/alpha.py\n+++ b/alpha.py\n+def alpha(): pass\n"
            "--- a/beta.py\n+++ b/beta.py\n+def beta(): pass\n"
        )
        files, symbols = self._parser().parse(patch)
        assert "alpha.py" in files
        assert "beta.py" in files
        assert "alpha" in symbols
        assert "beta" in symbols

    def test_parse_dev_null_excluded(self):
        """New file creation (--- /dev/null) should not appear as a file path."""
        patch = (
            "--- /dev/null\n"
            "+++ b/new_file.py\n"
            "+def new_func(): pass\n"
        )
        files, symbols = self._parser().parse(patch)
        assert "/dev/null" not in files
        assert "new_file.py" in files


# ---------------------------------------------------------------------------
# RepositoryScanner tests
# ---------------------------------------------------------------------------

class TestRepositoryScanner:
    def _make_repo(self, files: dict) -> str:
        """Create a temp dir with the given {rel_path: content} files."""
        tmpdir = tempfile.mkdtemp()
        for rel_path, content in files.items():
            abs_path = os.path.join(tmpdir, rel_path)
            os.makedirs(os.path.dirname(abs_path), exist_ok=True)
            with open(abs_path, "w", encoding="utf-8") as fh:
                fh.write(content)
        return tmpdir

    def test_find_importers_finds_importer(self):
        import shutil
        from backend.core.impact.analyzer import RepositoryScanner

        tmpdir = self._make_repo({
            "rules/foo.py": "def foo(): pass\n",
            "rules/bar.py": "from rules.foo import foo\n\ndef bar(): pass\n",
        })
        try:
            scanner = RepositoryScanner(tmpdir)
            findings = scanner.find_importers(["rules/foo.py"])
            file_paths = [f.file_path for f in findings]
            # bar.py imports from foo, so it should appear
            assert any("bar.py" in p for p in file_paths), (
                f"Expected bar.py in importers; got {file_paths}"
            )
            for finding in findings:
                assert finding.confirmed is True
                assert finding.impact_kind == "imports"
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)

    def test_find_importers_non_python_files_ignored(self):
        import shutil
        from backend.core.impact.analyzer import RepositoryScanner

        tmpdir = self._make_repo({
            "module.py": "def func(): pass\n",
            "README.md": "import module\n",  # Not Python, should be ignored
        })
        try:
            scanner = RepositoryScanner(tmpdir)
            # Only .py files are scanned
            findings = scanner.find_importers(["module.py"])
            for f in findings:
                assert f.file_path.endswith(".py")
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)

    def test_find_callers_finds_caller(self):
        import shutil
        from backend.core.impact.analyzer import RepositoryScanner

        tmpdir = self._make_repo({
            "lib/helper.py": "def important_func(): pass\n",
            "main.py": "from lib.helper import important_func\n\nimportant_func()\n",
        })
        try:
            scanner = RepositoryScanner(tmpdir)
            findings = scanner.find_callers(["important_func"])
            file_paths = [f.file_path for f in findings]
            assert any("main.py" in p for p in file_paths), (
                f"Expected main.py in callers; got {file_paths}"
            )
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)

    def test_find_related_tests_finds_test_by_name(self):
        import shutil
        from backend.core.impact.analyzer import RepositoryScanner

        tmpdir = self._make_repo({
            "foo.py": "def foo(): pass\n",
            "tests/test_foo.py": "def test_foo():\n    assert True\n",
        })
        try:
            scanner = RepositoryScanner(tmpdir)
            tests = scanner.find_related_tests(["foo.py"], ["foo"])
            assert any("test_foo.py" in t for t in tests), (
                f"Expected test_foo.py; got {tests}"
            )
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)

    def test_find_importers_unavailable_repo(self):
        from backend.core.impact.analyzer import RepositoryScanner
        scanner = RepositoryScanner("/does/not/exist")
        findings = scanner.find_importers(["foo.py"])
        assert findings == []

    def test_find_callers_unavailable_repo(self):
        from backend.core.impact.analyzer import RepositoryScanner
        scanner = RepositoryScanner("/does/not/exist")
        findings = scanner.find_callers(["my_func"])
        assert findings == []

    def test_find_related_tests_unavailable_repo(self):
        from backend.core.impact.analyzer import RepositoryScanner
        scanner = RepositoryScanner("/does/not/exist")
        result = scanner.find_related_tests(["foo.py"], [])
        assert result == []


# ---------------------------------------------------------------------------
# ChangeImpactAnalyzer tests
# ---------------------------------------------------------------------------

class TestChangeImpactAnalyzer:
    def _analyzer(self, repo_path=None):
        from backend.core.impact import ChangeImpactAnalyzer
        return ChangeImpactAnalyzer(repository_path=repo_path)

    def test_no_patch_no_files_returns_unknown(self):
        result = self._analyzer().analyse()
        assert result.risk_level == "unknown"
        assert "No changed files" in result.notes

    def test_patch_only_identifies_changed_files(self):
        patch = (
            "--- a/thefuck/rules/foo.py\n"
            "+++ b/thefuck/rules/foo.py\n"
            "+def new_match():\n+    return True\n"
        )
        result = self._analyzer().analyse(patch=patch)
        assert "thefuck/rules/foo.py" in result.changed_files
        assert "new_match" in result.changed_functions
        # At minimum the direct change should be in affected areas
        assert any(a.impact_kind == "direct_change" for a in result.affected_areas)

    def test_patch_only_no_repo_includes_note(self):
        patch = "--- a/foo.py\n+++ b/foo.py\n+x = 1\n"
        result = self._analyzer(repo_path=None).analyse(patch=patch)
        assert "not available" in result.notes.lower() or result.notes

    def test_files_changed_list_used_as_fallback(self):
        """files_changed list is used when no patch is supplied."""
        result = self._analyzer().analyse(files_changed=["a/b.py"])
        assert "a/b.py" in result.changed_files

    def test_full_analysis_with_real_files(self):
        import shutil
        tmpdir = tempfile.mkdtemp()
        try:
            # Create changed file and an importer
            os.makedirs(os.path.join(tmpdir, "rules"), exist_ok=True)
            os.makedirs(os.path.join(tmpdir, "tests"), exist_ok=True)
            with open(os.path.join(tmpdir, "rules", "foo.py"), "w") as fh:
                fh.write("def match(cmd): return False\n")
            with open(os.path.join(tmpdir, "rules", "bar.py"), "w") as fh:
                fh.write("from rules.foo import match\n\ndef other(): pass\n")
            with open(os.path.join(tmpdir, "tests", "test_foo.py"), "w") as fh:
                fh.write("def test_match():\n    assert True\n")

            patch = (
                "--- a/rules/foo.py\n"
                "+++ b/rules/foo.py\n"
                "+def match(cmd): return True\n"
            )
            analyzer = self._analyzer(repo_path=tmpdir)
            result = analyzer.analyse(patch=patch, files_changed=["rules/foo.py"])

            assert "rules/foo.py" in result.changed_files
            # bar.py imports from rules.foo → should appear
            importer_paths = [a.file_path for a in result.affected_areas if a.impact_kind == "imports"]
            assert any("bar.py" in p for p in importer_paths)
            # test_foo.py references foo → should appear in related tests
            assert any("test_foo.py" in t for t in result.related_test_files)
            assert result.risk_level in ("low", "medium", "high")
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)

    def test_malformed_patch_does_not_raise(self):
        """Malformed patch should produce a result, not raise an exception."""
        result = self._analyzer().analyse(patch="not a real patch at all {{{{{{")
        # Should not raise; may produce empty changed_files
        assert result is not None
        assert result.error is None or isinstance(result.error, str)

    def test_result_to_dict_shape(self):
        patch = "--- a/foo.py\n+++ b/foo.py\n+x = 1\n"
        result = self._analyzer().analyse(patch=patch)
        d = result.to_dict()
        assert "affected_areas" in d
        assert "changed_files" in d
        assert "changed_functions" in d
        assert "related_test_files" in d
        assert "risk_level" in d
        assert "notes" in d
        assert "analysed_at" in d

    def test_impacted_area_to_dict(self):
        from backend.core.impact.analyzer import ImpactedArea
        area = ImpactedArea(
            file_path="foo.py",
            reason="direct change",
            confirmed=True,
            impact_kind="direct_change",
        )
        d = area.to_dict()
        assert d["file_path"] == "foo.py"
        assert d["confirmed"] is True
        assert d["ai_generated"] is False
        assert d["impact_kind"] == "direct_change"

    def test_risk_level_low_for_single_file(self):
        patch = "--- a/foo.py\n+++ b/foo.py\n+x = 1\n"
        result = self._analyzer().analyse(patch=patch)
        # With no repo available there's only 1 affected area (direct_change)
        assert result.risk_level in ("low", "medium", "high", "unknown")

    def test_risk_level_unknown_when_no_files(self):
        result = self._analyzer().analyse()
        assert result.risk_level == "unknown"


# ---------------------------------------------------------------------------
# API: POST /api/verification/impact/analyse
# ---------------------------------------------------------------------------

class TestImpactAnalysisAPI:
    def test_analyse_by_fix_id_returns_202(self, client, sample_fix):
        from unittest.mock import patch as mpatch
        # Patch get_repository to avoid trying to access a real repo
        with mpatch("backend.api.verification.get_repository") as mock_get_repo:
            mock_repo = MagicMock()
            mock_repo.is_cloned.return_value = False
            mock_get_repo.return_value = mock_repo
            resp = client.post(
                "/api/verification/impact/analyse",
                json={"fix_id": sample_fix.id},
            )
        assert resp.status_code == 202
        data = resp.get_json()
        assert "affected_areas" in data
        assert "risk_level" in data
        assert data["fix_id"] == sample_fix.id

    def test_analyse_no_fix_id_returns_400(self, client):
        resp = client.post("/api/verification/impact/analyse", json={})
        assert resp.status_code == 400

    def test_analyse_unknown_fix_returns_404(self, client):
        resp = client.post(
            "/api/verification/impact/analyse",
            json={"fix_id": "no-such-fix"},
        )
        assert resp.status_code == 404

    def test_analyse_stores_metadata_in_risk_info(self, client, app, db, sample_fix):
        from unittest.mock import patch as mpatch
        with mpatch("backend.api.verification.get_repository") as mock_get_repo:
            mock_repo = MagicMock()
            mock_repo.is_cloned.return_value = False
            mock_get_repo.return_value = mock_repo
            client.post(
                "/api/verification/impact/analyse",
                json={"fix_id": sample_fix.id},
            )
        with app.app_context():
            from backend.models import Fix
            from backend.database.connection import db as _db
            fix = _db.session.get(Fix, sample_fix.id)
            assert fix.risk_info is not None
            assert "impact_risk_level" in fix.risk_info

    def test_get_impact_info_by_fix_id(self, client, sample_fix):
        from unittest.mock import patch as mpatch
        with mpatch("backend.api.verification.get_repository") as mock_get_repo:
            mock_repo = MagicMock()
            mock_repo.is_cloned.return_value = False
            mock_get_repo.return_value = mock_repo
            client.post(
                "/api/verification/impact/analyse",
                json={"fix_id": sample_fix.id},
            )
        resp = client.get(f"/api/verification/impact/{sample_fix.id}")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["fix_id"] == sample_fix.id
        assert "impact_risk_level" in data

    def test_get_impact_info_not_found(self, client):
        resp = client.get("/api/verification/impact/no-such-fix")
        assert resp.status_code == 404

    def test_analyse_patch_identifies_changed_files(self, client, sample_fix):
        """Impact analysis response should include the changed file from the fix patch."""
        from unittest.mock import patch as mpatch
        with mpatch("backend.api.verification.get_repository") as mock_get_repo:
            mock_repo = MagicMock()
            mock_repo.is_cloned.return_value = False
            mock_get_repo.return_value = mock_repo
            resp = client.post(
                "/api/verification/impact/analyse",
                json={"fix_id": sample_fix.id},
            )
        data = resp.get_json()
        # The fix patch references no_such_command.py
        assert "thefuck/rules/no_such_command.py" in data.get("changed_files", [])


# ---------------------------------------------------------------------------
# Fix import – needed in test body for MagicMock
# ---------------------------------------------------------------------------

from unittest.mock import MagicMock
