"""
Tests for migrations/versions/001_initial_schema.py.

These tests verify the migration file itself — its importability, its enum
label correctness, its table/column coverage, and its server_default forms —
without running the migration against any database.

Coverage
--------
1. Migration module imports cleanly.
2. revision / down_revision / branch_labels / depends_on are correct.
3. Every enum in the migration matches its corresponding Python Enum class
   (same name, same members, same order).
4. Every model table name is listed in the migration's upgrade().
5. Every model column name is present in the migration source.
6. server_default for enum columns uses sa.text (not a bare Python string)
   so PostgreSQL receives a properly-quoted SQL literal.
7. server_default for Boolean columns uses sa.text (not a bare string).
8. _make_enum helper sets create_type=False on every returned enum.
9. downgrade() drops tables and enums.
10. _ALL_ENUMS contains exactly the eight expected enum names.

No database connection, no Flask app context, and no network access are
required for any of these tests.
"""

import importlib
import inspect
import os
import sys
import types

import pytest
import sqlalchemy as sa

# Ensure project root is importable without installing the package.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))


# ---------------------------------------------------------------------------
# Load the migration module under a stable name
# ---------------------------------------------------------------------------

def _load_migration():
    """Import migrations/versions/001_initial_schema.py as a module."""
    base = os.path.dirname(__file__)
    migration_path = os.path.join(
        base, "..", "..", "migrations", "versions", "001_initial_schema.py"
    )
    migration_path = os.path.normpath(migration_path)
    spec = importlib.util.spec_from_file_location(
        "migration_001_initial_schema", migration_path
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def migration():
    return _load_migration()


# ---------------------------------------------------------------------------
# Expected shapes derived from the Python Enum classes
# ---------------------------------------------------------------------------

def _enum_values(enum_cls):
    """Return a list of the string values of a Python Enum, in definition order."""
    return [m.value for m in enum_cls]


# ---------------------------------------------------------------------------
# 1. Migration importability
# ---------------------------------------------------------------------------

class TestMigrationImports:
    def test_module_loads_without_error(self, migration):
        assert migration is not None

    def test_upgrade_is_callable(self, migration):
        assert callable(migration.upgrade)

    def test_downgrade_is_callable(self, migration):
        assert callable(migration.downgrade)

    def test_make_enum_helper_present(self, migration):
        assert hasattr(migration, '_make_enum')
        assert callable(migration._make_enum)

    def test_all_enums_list_present(self, migration):
        assert hasattr(migration, '_ALL_ENUMS')
        assert isinstance(migration._ALL_ENUMS, list)


# ---------------------------------------------------------------------------
# 2. Revision metadata
# ---------------------------------------------------------------------------

class TestRevisionMetadata:
    def test_revision_is_001(self, migration):
        assert migration.revision == '001'

    def test_down_revision_is_none(self, migration):
        assert migration.down_revision is None

    def test_branch_labels_is_none(self, migration):
        assert migration.branch_labels is None

    def test_depends_on_is_none(self, migration):
        assert migration.depends_on is None


# ---------------------------------------------------------------------------
# 3. Enum labels match Python Enum classes
# ---------------------------------------------------------------------------

class TestEnumLabels:
    """Each sa.Enum in the migration must have exactly the same labels as the
    corresponding Python Enum, in the same order."""

    def _get_enum(self, migration, attr_name):
        enum = getattr(migration, attr_name)
        assert isinstance(enum, sa.Enum), f"{attr_name!r} is not an sa.Enum"
        return enum

    def test_target_repository_enum_labels(self, migration):
        from backend.models.bug import TargetRepository
        enum = self._get_enum(migration, '_target_repository_enum')
        assert list(enum.enums) == _enum_values(TargetRepository)

    def test_bug_status_enum_labels(self, migration):
        from backend.models.bug import BugStatus
        enum = self._get_enum(migration, '_bug_status_enum')
        assert list(enum.enums) == _enum_values(BugStatus)

    def test_investigation_status_enum_labels(self, migration):
        from backend.models.investigation import InvestigationStatus
        enum = self._get_enum(migration, '_investigation_status_enum')
        assert list(enum.enums) == _enum_values(InvestigationStatus)

    def test_evidence_type_enum_labels(self, migration):
        from backend.models.evidence import EvidenceType
        enum = self._get_enum(migration, '_evidence_type_enum')
        assert list(enum.enums) == _enum_values(EvidenceType)

    def test_approval_status_enum_labels(self, migration):
        from backend.models.fix import ApprovalStatus
        enum = self._get_enum(migration, '_approval_status_enum')
        assert list(enum.enums) == _enum_values(ApprovalStatus)

    def test_application_status_enum_labels(self, migration):
        from backend.models.fix import ApplicationStatus
        enum = self._get_enum(migration, '_application_status_enum')
        assert list(enum.enums) == _enum_values(ApplicationStatus)

    def test_test_run_kind_enum_labels(self, migration):
        from backend.models.verification import TestRunKind
        enum = self._get_enum(migration, '_test_run_kind_enum')
        assert list(enum.enums) == _enum_values(TestRunKind)

    def test_test_run_status_enum_labels(self, migration):
        from backend.models.verification import TestRunStatus
        enum = self._get_enum(migration, '_test_run_status_enum')
        assert list(enum.enums) == _enum_values(TestRunStatus)


# ---------------------------------------------------------------------------
# 4. Enum names match the SAEnum name= arguments in the models
# ---------------------------------------------------------------------------

class TestEnumNames:
    def test_target_repository_enum_name(self, migration):
        assert migration._target_repository_enum.name == 'target_repository_enum'

    def test_bug_status_enum_name(self, migration):
        assert migration._bug_status_enum.name == 'bug_status_enum'

    def test_investigation_status_enum_name(self, migration):
        assert migration._investigation_status_enum.name == 'investigation_status_enum'

    def test_evidence_type_enum_name(self, migration):
        assert migration._evidence_type_enum.name == 'evidence_type_enum'

    def test_approval_status_enum_name(self, migration):
        assert migration._approval_status_enum.name == 'approval_status_enum'

    def test_application_status_enum_name(self, migration):
        assert migration._application_status_enum.name == 'application_status_enum'

    def test_test_run_kind_enum_name(self, migration):
        assert migration._test_run_kind_enum.name == 'test_run_kind_enum'

    def test_test_run_status_enum_name(self, migration):
        assert migration._test_run_status_enum.name == 'test_run_status_enum'


# ---------------------------------------------------------------------------
# 5. _make_enum sets create_type=False
# ---------------------------------------------------------------------------

class TestMakeEnumHelper:
    def test_all_enums_have_create_type_false(self, migration):
        for enum in migration._ALL_ENUMS:
            assert enum.create_type is False, (
                f"Enum {enum.name!r} has create_type={enum.create_type!r}; "
                "must be False so op.create_table does not attempt to issue "
                "CREATE TYPE inside the table DDL"
            )

    def test_all_enums_count(self, migration):
        assert len(migration._ALL_ENUMS) == 8

    def test_all_enums_names_present(self, migration):
        names = {e.name for e in migration._ALL_ENUMS}
        expected = {
            'target_repository_enum', 'bug_status_enum',
            'investigation_status_enum', 'evidence_type_enum',
            'approval_status_enum', 'application_status_enum',
            'test_run_kind_enum', 'test_run_status_enum',
        }
        assert names == expected


# ---------------------------------------------------------------------------
# 6 & 7. server_default correctness (must use sa.text, not bare strings)
# ---------------------------------------------------------------------------

class TestServerDefaults:
    """
    Parse the migration source and verify that every server_default= argument
    that supplies a value is wrapped in sa.text(), not a bare Python string.

    A bare Python string like server_default='PENDING' is passed as-is to the
    database DEFAULT clause.  On PostgreSQL this produces DEFAULT PENDING
    (an unquoted identifier) which fails for enum columns.  The correct form
    is server_default=sa.text("'PENDING'") which produces DEFAULT 'PENDING'
    (a quoted SQL string literal).
    """

    def _get_source(self, migration):
        return inspect.getsource(migration)

    def test_no_bare_string_server_defaults_for_enum_columns(self, migration):
        """
        Enum column server_default must NOT be a bare Python string.
        Detect the pattern: server_default='<VALUE>' where VALUE is an enum label.
        """
        source = self._get_source(migration)
        enum_labels = [
            'RECEIVED', 'INVESTIGATING', 'ROOT_CAUSE_FOUND', 'FIX_GENERATED',
            'WAITING_FOR_APPROVAL', 'FIX_APPLIED', 'VERIFYING',
            'COMPLETED', 'FAILED', 'CANCELLED',
            'PENDING', 'IN_PROGRESS',
            'SOURCE_CODE', 'COMMAND_OUTPUT', 'TEST_RESULT', 'DIFF',
            'LOG_ENTRY', 'OTHER',
            'APPROVED', 'REJECTED',
            'NOT_APPLIED', 'APPLYING', 'APPLIED',
            'BUG_REPRODUCTION', 'REGRESSION', 'GENERATED_REGRESSION',
            'EXPLORATORY', 'RUNNING', 'PASSED', 'ERROR',
        ]
        for label in enum_labels:
            # Pattern: server_default='LABEL' or server_default="LABEL"
            bare_single = f"server_default='{label}'"
            bare_double = f'server_default="{label}"'
            assert bare_single not in source, (
                f"Bare server_default='{label}' found – must use "
                f"sa.text(\"'{label}'\")"
            )
            assert bare_double not in source, (
                f'Bare server_default="{label}" found – must use '
                f"sa.text(\"'{label}'\")"
            )

    def test_no_bare_string_server_defaults_for_boolean_columns(self, migration):
        """
        Boolean server_default must not be a bare Python 'true'/'false' string.
        While PostgreSQL accepts bare 'true'/'false' in DEFAULT clauses for
        boolean columns, using sa.text() is explicit and consistent.
        """
        source = self._get_source(migration)
        for val in ("'true'", "'false'"):
            # Detect: server_default='true' (bare Python string)
            pattern = f"server_default={val}"
            assert pattern not in source, (
                f"Bare {pattern} found – must use sa.text({val})"
            )

    def test_sa_text_used_for_enum_server_defaults(self, migration):
        """Confirm sa.text() is used for every enum server_default."""
        source = self._get_source(migration)
        expected_defaults = [
            "sa.text(\"'RECEIVED'\")",
            "sa.text(\"'PENDING'\")",
            "sa.text(\"'NOT_APPLIED'\")",
            "sa.text(\"'REGRESSION'\")",
        ]
        for expected in expected_defaults:
            assert expected in source, (
                f"Expected {expected!r} not found in migration source"
            )

    def test_sa_text_used_for_boolean_server_defaults(self, migration):
        """Confirm sa.text() is used for every boolean server_default."""
        source = self._get_source(migration)
        assert "sa.text('true')" in source
        assert "sa.text('false')" in source


# ---------------------------------------------------------------------------
# 8. Table and column coverage against models
# ---------------------------------------------------------------------------

class TestTableAndColumnCoverage:
    """
    Parse the migration source text and confirm every model table and every
    model column appears at least once.

    This is a lightweight text-search check, not a full AST parse.  It catches
    omitted tables or obviously missing columns.
    """

    def _source(self, migration):
        return inspect.getsource(migration)

    def test_all_table_names_present(self, migration):
        source = self._source(migration)
        expected_tables = [
            'bugs', 'investigations', 'hypotheses', 'evidence',
            'fixes', 'test_runs', 'reports',
        ]
        for table in expected_tables:
            assert f"'{table}'" in source or f'"{table}"' in source, (
                f"Table name {table!r} not found in migration source"
            )

    def _check_columns(self, migration, table_name, column_names):
        source = self._source(migration)
        # Find the op.create_table block for this table in the source.
        # We search for each column name within the whole migration source
        # (this is conservative but sufficient as column names are unique
        # across tables in this schema or clearly scoped).
        for col in column_names:
            assert f"'{col}'" in source, (
                f"Column {col!r} for table {table_name!r} not found "
                "in migration source"
            )

    def test_bugs_columns(self, migration):
        self._check_columns(migration, 'bugs', [
            'id', 'title', 'description', 'target_repository',
            'repository_ref', 'error_message', 'extra_context',
            'status', 'created_at', 'updated_at',
        ])

    def test_investigations_columns(self, migration):
        self._check_columns(migration, 'investigations', [
            'id', 'bug_id', 'status', 'summary',
            'relevant_files', 'relevant_functions',
            'suspected_cause', 'root_cause_explanation', 'confidence',
            'root_cause_ai_generated', 'root_cause_machine_supported',
            'execution_context', 'error_details',
            'started_at', 'completed_at', 'created_at',
        ])

    def test_hypotheses_columns(self, migration):
        self._check_columns(migration, 'hypotheses', [
            'id', 'investigation_id', 'title', 'explanation',
            'confidence', 'ai_generated', 'machine_corroborated', 'created_at',
        ])

    def test_evidence_columns(self, migration):
        self._check_columns(migration, 'evidence', [
            'id', 'investigation_id', 'evidence_type', 'description',
            'file_path', 'line_number', 'line_number_end', 'code_snippet',
            'search_query', 'relevance_explanation',
            'command_executed', 'command_output', 'exit_code',
            'test_name', 'test_passed', 'test_output',
            'is_verified', 'collected_at',
        ])

    def test_fixes_columns(self, migration):
        self._check_columns(migration, 'fixes', [
            'id', 'investigation_id', 'ai_generated',
            'explanation', 'patch', 'files_changed',
            'affected_lines', 'supporting_evidence_ids',
            'confidence', 'risk_info',
            'approval_required', 'approval_status',
            'approved_by', 'approved_at', 'rejection_reason',
            'application_status', 'applied_at', 'application_error',
            'created_at', 'updated_at',
        ])

    def test_test_runs_columns(self, migration):
        self._check_columns(migration, 'test_runs', [
            'id', 'fix_id', 'bug_id', 'kind', 'status',
            'command', 'output', 'exit_code',
            'tests_total', 'tests_passed', 'tests_failed', 'tests_errored',
            'result_data', 'started_at', 'completed_at', 'created_at',
        ])

    def test_reports_columns(self, migration):
        self._check_columns(migration, 'reports', [
            'id', 'bug_id', 'fix_confidence',
            'confidence_machine_verified', 'content', 'summary',
            'generated_at', 'created_at',
        ])


# ---------------------------------------------------------------------------
# 9. Foreign key ondelete actions
# ---------------------------------------------------------------------------

class TestForeignKeys:
    def _source(self, migration):
        return inspect.getsource(migration)

    def _count_occurrences(self, source, substring):
        return source.count(substring)

    def test_cascade_deletes_present(self, migration):
        """CASCADE deletes must appear for all the FK columns the models declare."""
        source = self._source(migration)
        # bugs.id is referenced with CASCADE by investigations, reports
        assert source.count("ondelete='CASCADE'") >= 5, (
            "Expected at least 5 CASCADE FK references "
            "(investigations.bug_id, hypotheses.investigation_id, "
            "evidence.investigation_id, fixes.investigation_id, reports.bug_id)"
        )

    def test_set_null_on_test_runs_fix_id(self, migration):
        source = self._source(migration)
        assert "ondelete='SET NULL'" in source, (
            "test_runs.fix_id and test_runs.bug_id must use ondelete='SET NULL'"
        )

    def test_set_null_count(self, migration):
        source = self._source(migration)
        assert source.count("ondelete='SET NULL'") == 2, (
            "Exactly 2 SET NULL FK references expected: "
            "test_runs.fix_id and test_runs.bug_id"
        )


# ---------------------------------------------------------------------------
# 10. Index coverage
# ---------------------------------------------------------------------------

class TestIndexes:
    def _source(self, migration):
        return inspect.getsource(migration)

    def test_all_expected_indexes_defined(self, migration):
        source = self._source(migration)
        expected_indexes = [
            'ix_investigations_bug_id',
            'ix_hypotheses_investigation_id',
            'ix_evidence_investigation_id',
            'ix_fixes_investigation_id',
            'ix_test_runs_fix_id',
            'ix_test_runs_bug_id',
            'ix_reports_bug_id',
        ]
        for idx in expected_indexes:
            assert idx in source, f"Index {idx!r} not found in migration"

    def test_all_indexes_dropped_in_downgrade(self, migration):
        source = self._source(migration)
        expected_indexes = [
            'ix_investigations_bug_id',
            'ix_hypotheses_investigation_id',
            'ix_evidence_investigation_id',
            'ix_fixes_investigation_id',
            'ix_test_runs_fix_id',
            'ix_test_runs_bug_id',
            'ix_reports_bug_id',
        ]
        # Each index name appears twice: once in create_index, once in drop_index
        for idx in expected_indexes:
            count = source.count(idx)
            assert count >= 2, (
                f"Index {idx!r} appears {count} time(s); expected at least 2 "
                "(one create_index and one drop_index)"
            )


# ---------------------------------------------------------------------------
# 11. downgrade() completeness
# ---------------------------------------------------------------------------

class TestDowngrade:
    def _source(self, migration):
        return inspect.getsource(migration)

    def test_all_tables_dropped_in_downgrade(self, migration):
        source = self._source(migration)
        for table in ['bugs', 'investigations', 'hypotheses',
                      'evidence', 'fixes', 'test_runs', 'reports']:
            assert f"drop_table('{table}')" in source or \
                   f'drop_table("{table}")' in source, (
                f"Table {table!r} not dropped in downgrade()"
            )

    def test_enums_dropped_in_downgrade(self, migration):
        source = self._source(migration)
        assert 'reversed(_ALL_ENUMS)' in source, (
            "downgrade() must iterate reversed(_ALL_ENUMS) to drop enum types"
        )
        assert '.drop(' in source, (
            "downgrade() must call .drop() on enum objects"
        )
