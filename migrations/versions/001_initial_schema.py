"""Initial schema – all BugProof tables and enums.

Revision ID: 001
Revises:
Create Date: 2025-01-01 00:00:00.000000

Tables created:
    bugs, investigations, hypotheses, evidence, fixes, test_runs, reports

Enum types created (PostgreSQL only; SQLite uses inline VARCHAR):
    target_repository_enum, bug_status_enum, investigation_status_enum,
    evidence_type_enum, approval_status_enum, application_status_enum,
    test_run_kind_enum, test_run_status_enum

Design notes
------------
* Enum types are created explicitly before the first table that uses them
  (via sa.Enum.create()) and dropped explicitly in downgrade().  This is
  required for PostgreSQL where named TYPE objects must exist before any
  column references them.  On SQLite the create/drop calls are no-ops.

* server_default values for enum columns use the PostgreSQL string-literal
  form "'VALUE'" (a SQL string literal, not a bare identifier) so that
  PostgreSQL parses them correctly.  Bare identifiers like PENDING (without
  quotes) are rejected by PostgreSQL's DEFAULT clause parser.

* server_default values for Boolean columns use 'true' / 'false' (lowercase),
  which PostgreSQL accepts as boolean literals in DEFAULT expressions.

* Column order within each table matches the model definition order exactly.

* All foreign keys carry the same ondelete action as in the SQLAlchemy models.

* Indexes are created on every FK column to match what autogenerate would
  produce and to support efficient cascade-delete queries.
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '001'
down_revision = None
branch_labels = None
depends_on = None


# ---------------------------------------------------------------------------
# Enum type helpers
# ---------------------------------------------------------------------------
# Each enum is constructed once here and reused in both upgrade() and
# downgrade() so the name is always consistent.
#
# create_type=False is used when passing an enum to sa.Column inside
# op.create_table because op.create_table does NOT automatically emit
# CREATE TYPE – we handle that ourselves via enum.create() below.
# ---------------------------------------------------------------------------

def _make_enum(name, *values):
    """Return an sa.Enum with the given name, without auto-creating the type."""
    return sa.Enum(*values, name=name, create_type=False)


_target_repository_enum = _make_enum(
    'target_repository_enum',
    'thefuck', 'tqdm', 'youtube_dl',
)
_bug_status_enum = _make_enum(
    'bug_status_enum',
    'RECEIVED', 'INVESTIGATING', 'ROOT_CAUSE_FOUND', 'FIX_GENERATED',
    'WAITING_FOR_APPROVAL', 'FIX_APPLIED', 'VERIFYING',
    'COMPLETED', 'FAILED', 'CANCELLED',
)
_investigation_status_enum = _make_enum(
    'investigation_status_enum',
    'PENDING', 'IN_PROGRESS', 'COMPLETED', 'FAILED',
)
_evidence_type_enum = _make_enum(
    'evidence_type_enum',
    'SOURCE_CODE', 'COMMAND_OUTPUT', 'TEST_RESULT', 'DIFF', 'LOG_ENTRY', 'OTHER',
)
_approval_status_enum = _make_enum(
    'approval_status_enum',
    'PENDING', 'APPROVED', 'REJECTED',
)
_application_status_enum = _make_enum(
    'application_status_enum',
    'NOT_APPLIED', 'APPLYING', 'APPLIED', 'FAILED',
)
_test_run_kind_enum = _make_enum(
    'test_run_kind_enum',
    'BUG_REPRODUCTION', 'REGRESSION', 'GENERATED_REGRESSION', 'EXPLORATORY',
)
_test_run_status_enum = _make_enum(
    'test_run_status_enum',
    'PENDING', 'RUNNING', 'PASSED', 'FAILED', 'ERROR',
)

# Ordered list of (enum, first_table_that_uses_it) used by upgrade/downgrade.
# Enums must be created in dependency order (parents before children).
_ALL_ENUMS = [
    _target_repository_enum,
    _bug_status_enum,
    _investigation_status_enum,
    _evidence_type_enum,
    _approval_status_enum,
    _application_status_enum,
    _test_run_kind_enum,
    _test_run_status_enum,
]


def upgrade():
    bind = op.get_bind()

    # ------------------------------------------------------------------ #
    # Create PostgreSQL TYPE objects before any table references them.
    # On SQLite these calls are silent no-ops.
    # ------------------------------------------------------------------ #
    for enum in _ALL_ENUMS:
        enum.create(bind, checkfirst=True)

    # ------------------------------------------------------------------ #
    # bugs
    # Matches: backend/models/bug.py  class Bug
    # ------------------------------------------------------------------ #
    op.create_table(
        'bugs',
        sa.Column('id', sa.String(36), primary_key=True, nullable=False),
        sa.Column('title', sa.String(255), nullable=False),
        sa.Column('description', sa.Text, nullable=False),
        # target_repository: no server_default; value is required at INSERT
        sa.Column('target_repository', _target_repository_enum, nullable=False),
        sa.Column('repository_ref', sa.String(255), nullable=True),
        sa.Column('error_message', sa.Text, nullable=True),
        sa.Column('extra_context', sa.JSON, nullable=True),
        # server_default uses a SQL string literal ('RECEIVED') so PostgreSQL
        # parses the default as the text value, not a bare identifier.
        sa.Column('status', _bug_status_enum, nullable=False,
                  server_default=sa.text("'RECEIVED'")),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    )

    # ------------------------------------------------------------------ #
    # investigations
    # Matches: backend/models/investigation.py  class Investigation
    # ------------------------------------------------------------------ #
    op.create_table(
        'investigations',
        sa.Column('id', sa.String(36), primary_key=True, nullable=False),
        sa.Column('bug_id', sa.String(36),
                  sa.ForeignKey('bugs.id', ondelete='CASCADE'), nullable=False),
        sa.Column('status', _investigation_status_enum, nullable=False,
                  server_default=sa.text("'PENDING'")),
        sa.Column('summary', sa.Text, nullable=True),
        sa.Column('relevant_files', sa.JSON, nullable=True),
        sa.Column('relevant_functions', sa.JSON, nullable=True),
        sa.Column('suspected_cause', sa.Text, nullable=True),
        sa.Column('root_cause_explanation', sa.Text, nullable=True),
        sa.Column('confidence', sa.Float, nullable=True),
        # Boolean defaults: PostgreSQL accepts unquoted true/false literals
        sa.Column('root_cause_ai_generated', sa.Boolean, nullable=False,
                  server_default=sa.text('true')),
        sa.Column('root_cause_machine_supported', sa.Boolean, nullable=False,
                  server_default=sa.text('false')),
        sa.Column('execution_context', sa.JSON, nullable=True),
        sa.Column('error_details', sa.Text, nullable=True),
        sa.Column('started_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('completed_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index('ix_investigations_bug_id', 'investigations', ['bug_id'])

    # ------------------------------------------------------------------ #
    # hypotheses
    # Matches: backend/models/investigation.py  class Hypothesis
    # ------------------------------------------------------------------ #
    op.create_table(
        'hypotheses',
        sa.Column('id', sa.String(36), primary_key=True, nullable=False),
        sa.Column('investigation_id', sa.String(36),
                  sa.ForeignKey('investigations.id', ondelete='CASCADE'),
                  nullable=False),
        sa.Column('title', sa.String(255), nullable=False),
        sa.Column('explanation', sa.Text, nullable=False),
        sa.Column('confidence', sa.Float, nullable=True),
        sa.Column('ai_generated', sa.Boolean, nullable=False,
                  server_default=sa.text('true')),
        sa.Column('machine_corroborated', sa.Boolean, nullable=False,
                  server_default=sa.text('false')),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index('ix_hypotheses_investigation_id', 'hypotheses',
                    ['investigation_id'])

    # ------------------------------------------------------------------ #
    # evidence
    # Matches: backend/models/evidence.py  class Evidence
    # ------------------------------------------------------------------ #
    op.create_table(
        'evidence',
        sa.Column('id', sa.String(36), primary_key=True, nullable=False),
        sa.Column('investigation_id', sa.String(36),
                  sa.ForeignKey('investigations.id', ondelete='CASCADE'),
                  nullable=False),
        sa.Column('evidence_type', _evidence_type_enum, nullable=False),
        sa.Column('description', sa.Text, nullable=False),
        sa.Column('file_path', sa.Text, nullable=True),
        sa.Column('line_number', sa.Integer, nullable=True),
        sa.Column('line_number_end', sa.Integer, nullable=True),
        sa.Column('code_snippet', sa.Text, nullable=True),
        sa.Column('search_query', sa.Text, nullable=True),
        sa.Column('relevance_explanation', sa.Text, nullable=True),
        sa.Column('command_executed', sa.Text, nullable=True),
        sa.Column('command_output', sa.Text, nullable=True),
        sa.Column('exit_code', sa.Integer, nullable=True),
        sa.Column('test_name', sa.String(512), nullable=True),
        sa.Column('test_passed', sa.Boolean, nullable=True),
        sa.Column('test_output', sa.Text, nullable=True),
        sa.Column('is_verified', sa.Boolean, nullable=False,
                  server_default=sa.text('false')),
        sa.Column('collected_at', sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index('ix_evidence_investigation_id', 'evidence',
                    ['investigation_id'])

    # ------------------------------------------------------------------ #
    # fixes
    # Matches: backend/models/fix.py  class Fix
    # ------------------------------------------------------------------ #
    op.create_table(
        'fixes',
        sa.Column('id', sa.String(36), primary_key=True, nullable=False),
        sa.Column('investigation_id', sa.String(36),
                  sa.ForeignKey('investigations.id', ondelete='CASCADE'),
                  nullable=False),
        sa.Column('ai_generated', sa.Boolean, nullable=False,
                  server_default=sa.text('true')),
        sa.Column('explanation', sa.Text, nullable=True),
        sa.Column('patch', sa.Text, nullable=True),
        sa.Column('files_changed', sa.JSON, nullable=True),
        sa.Column('affected_lines', sa.JSON, nullable=True),
        sa.Column('supporting_evidence_ids', sa.JSON, nullable=True),
        sa.Column('confidence', sa.Float, nullable=True),
        sa.Column('risk_info', sa.JSON, nullable=True),
        sa.Column('approval_required', sa.Boolean, nullable=False,
                  server_default=sa.text('true')),
        sa.Column('approval_status', _approval_status_enum, nullable=False,
                  server_default=sa.text("'PENDING'")),
        sa.Column('approved_by', sa.String(255), nullable=True),
        sa.Column('approved_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('rejection_reason', sa.Text, nullable=True),
        sa.Column('application_status', _application_status_enum, nullable=False,
                  server_default=sa.text("'NOT_APPLIED'")),
        sa.Column('applied_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('application_error', sa.Text, nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index('ix_fixes_investigation_id', 'fixes', ['investigation_id'])

    # ------------------------------------------------------------------ #
    # test_runs
    # Matches: backend/models/verification.py  class TestRun
    # ------------------------------------------------------------------ #
    op.create_table(
        'test_runs',
        sa.Column('id', sa.String(36), primary_key=True, nullable=False),
        sa.Column('fix_id', sa.String(36),
                  sa.ForeignKey('fixes.id', ondelete='SET NULL'), nullable=True),
        sa.Column('bug_id', sa.String(36),
                  sa.ForeignKey('bugs.id', ondelete='SET NULL'), nullable=True),
        sa.Column('kind', _test_run_kind_enum, nullable=False,
                  server_default=sa.text("'REGRESSION'")),
        sa.Column('status', _test_run_status_enum, nullable=False,
                  server_default=sa.text("'PENDING'")),
        sa.Column('command', sa.Text, nullable=True),
        sa.Column('output', sa.Text, nullable=True),
        sa.Column('exit_code', sa.Integer, nullable=True),
        sa.Column('tests_total', sa.Integer, nullable=True),
        sa.Column('tests_passed', sa.Integer, nullable=True),
        sa.Column('tests_failed', sa.Integer, nullable=True),
        sa.Column('tests_errored', sa.Integer, nullable=True),
        sa.Column('result_data', sa.JSON, nullable=True),
        sa.Column('started_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('completed_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index('ix_test_runs_fix_id', 'test_runs', ['fix_id'])
    op.create_index('ix_test_runs_bug_id', 'test_runs', ['bug_id'])

    # ------------------------------------------------------------------ #
    # reports
    # Matches: backend/models/verification.py  class Report
    # ------------------------------------------------------------------ #
    op.create_table(
        'reports',
        sa.Column('id', sa.String(36), primary_key=True, nullable=False),
        sa.Column('bug_id', sa.String(36),
                  sa.ForeignKey('bugs.id', ondelete='CASCADE'), nullable=False),
        sa.Column('fix_confidence', sa.Float, nullable=True),
        sa.Column('confidence_machine_verified', sa.Boolean, nullable=False,
                  server_default=sa.text('false')),
        sa.Column('content', sa.JSON, nullable=True),
        sa.Column('summary', sa.Text, nullable=True),
        sa.Column('generated_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index('ix_reports_bug_id', 'reports', ['bug_id'])


def downgrade():
    # Drop tables in reverse dependency order (children before parents).
    op.drop_index('ix_reports_bug_id', table_name='reports')
    op.drop_table('reports')

    op.drop_index('ix_test_runs_bug_id', table_name='test_runs')
    op.drop_index('ix_test_runs_fix_id', table_name='test_runs')
    op.drop_table('test_runs')

    op.drop_index('ix_fixes_investigation_id', table_name='fixes')
    op.drop_table('fixes')

    op.drop_index('ix_evidence_investigation_id', table_name='evidence')
    op.drop_table('evidence')

    op.drop_index('ix_hypotheses_investigation_id', table_name='hypotheses')
    op.drop_table('hypotheses')

    op.drop_index('ix_investigations_bug_id', table_name='investigations')
    op.drop_table('investigations')

    op.drop_table('bugs')

    # Drop PostgreSQL TYPE objects in reverse creation order.
    # On SQLite these are no-ops.
    bind = op.get_bind()
    for enum in reversed(_ALL_ENUMS):
        enum.drop(bind, checkfirst=True)
