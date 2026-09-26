# BugProof – Architecture

## Purpose

BugProof is a developer debugging workflow tool that guides engineers from a raw bug report to a minimal, safe, evidence-backed fix. It combines machine-verified execution evidence with AI-assisted reasoning to produce structured root cause analyses and fix proposals.

---

## Technology Stack

| Layer | Technology |
|---|---|
| Backend API | Python 3.11+, Flask 3 |
| Database | PostgreSQL (via SQLAlchemy 2 + Flask-SQLAlchemy) |
| Schema migrations | Flask-Migrate (Alembic) |
| Process execution | Python `subprocess` |
| Git operations | git CLI (via subprocess wrapper) |
| AI reasoning | IBM watsonx (Granite model via REST API) |
| Frontend (future) | React (Team Member 3) |
| Testing | pytest, pytest-flask |

---

## High-Level Component Architecture

```
React UI  (Team Member 3 – not yet implemented)
    │
    │  HTTP/JSON
    ▼
Flask Backend / API
    │
    ▼
BugProof Core Engine
    ├── Investigation Pipeline
    ├── Root Cause Analyser
    ├── Fix Generator
    └── Workflow Orchestrator
    │
    ├── AI Interfaces (IBM watsonx)
    │   ├── AIInvestigator
    │   ├── AIRootCause
    │   └── AIFixer
    │
    ├── Repository Abstraction
    │   ├── BaseRepository (interface)
    │   ├── TheFuckRepository
    │   ├── TqdmRepository
    │   └── YoutubeDlRepository
    │
    └── Execution Layer
        ├── CommandRunner
        ├── GitRunner
        ├── TestRunner
        └── LocalSandbox
    │
    ▼
PostgreSQL
(metadata, evidence, statuses, hypotheses, fixes, test-run records)
```

---

## Backend Directory Structure

```
backend/
│
├── app.py                    Flask application factory
│
├── api/                      HTTP route handlers (thin – orchestration lives in core)
│   ├── bugs.py               POST /api/bugs, GET /api/bugs, GET /api/bugs/<id>
│   ├── investigations.py     GET /api/investigations/<id>, evidence, hypotheses
│   ├── fixes.py              GET/approve/reject/apply fix endpoints
│   ├── verification.py       Test-run retrieval and trigger (Member 2 integration point)
│   └── reports.py            Report retrieval (Member 3 integration point)
│
├── core/                     Business logic and pipeline orchestration
│   ├── investigation/        Smart Bug Investigation pipeline
│   ├── root_cause/           Evidence-Based Root Cause Analyser
│   ├── fix_generation/       Minimal and Safe Fix Generator
│   └── workflow/             Debugging run lifecycle / orchestrator
│
├── repositories/             Target repository adapters
│   ├── base.py               BaseRepository abstract interface + TestSpec / RepositoryInfo
│   ├── thefuck.py            TheFuckRepository adapter
│   ├── tqdm.py               TqdmRepository adapter
│   └── youtube_dl.py         YoutubeDlRepository adapter
│
├── execution/                Subprocess and git execution utilities
│   ├── runner.py             CommandRunner (subprocess wrapper)
│   ├── git.py                GitRunner (git CLI wrapper)
│   ├── tests.py              TestRunner (TestSpec → result dict)
│   └── sandbox.py            LocalSandbox (temp-copy isolation)
│
├── ai/                       IBM watsonx AI service interfaces
│   ├── investigator.py       AIInvestigator
│   ├── root_cause.py         AIRootCause
│   └── fixer.py              AIFixer
│
├── models/                   SQLAlchemy ORM models
│   ├── bug.py                Bug, BugStatus, TargetRepository
│   ├── investigation.py      Investigation, InvestigationStatus, Hypothesis
│   ├── evidence.py           Evidence, EvidenceType
│   ├── fix.py                Fix, ApprovalStatus, ApplicationStatus
│   └── verification.py       TestRun, TestRunKind, TestRunStatus, Report
│
├── database/
│   ├── connection.py         db / migrate singleton instances + init_db()
│   └── migrations/           Flask-Migrate / Alembic migration scripts
│
├── config/
│   └── settings.py           Config classes; all secrets via env vars
│
└── tests/
    └── test_foundation.py    Foundation validation test suite
```

---

## End-to-End Workflow

```
Bug Report (POST /api/bugs)
    │
    ▼
Bug stored (status = RECEIVED)
    │
POST /api/bugs/<id>/start
    │
    ▼
WorkflowOrchestrator.execute()      ← runs in background daemon thread
    │
    ├── InvestigationPipeline.run()
    │   ├── _gather_execution_context()       [machine-verified: git log/status]
    │   ├── _build_keywords()                 [heuristic: error + test + description]
    │   ├── _resolve_test_focus()             [machine-verified: file existence check]
    │   ├── _search_source_files()            [machine-verified: file read]
    │   ├── _search_functions()               [machine-verified: file read]
    │   ├── _collect_file_evidence()          [machine-verified: file read + line numbers]
    │   │     Evidence carries: file_path, line_number, line_number_end,
    │   │                       code_snippet, search_query, relevance_explanation
    │   ├── _collect_command_evidence()       [machine-verified: git log, git status, grep]
    │   └── AI.analyse()                      [AI-generated, marked as such]
    │
    │   Evidence items flushed to DB with IDs before RCA/fix generation
    │
    ├── RootCauseAnalyser.analyse()
    │   ├── AI.analyse()                      [AI-generated, marked as such]
    │   ├── Produces: summary, suspected_cause, mechanism, affected_files/functions
    │   ├── supporting_evidence_ids           [IDs of verified Evidence records]
    │   └── status: confirmed_by_evidence | suspected | insufficient_evidence
    │
    └── FixGenerator.generate()
        ├── supporting_evidence_ids           [from verified Evidence records]
        ├── affected_lines                    [from SOURCE_CODE evidence]
        ├── confidence                        [AI-reported]
        └── AI.generate_fix()                 [AI-generated patch, PENDING approval]

Developer reviews patch via GET /api/fixes/<id>
Developer approves via POST /api/fixes/<id>/approve
Fix applied via POST /api/fixes/<id>/apply   [only after APPROVED]

[Team Member 2] POST /api/verification/run   → TestRun created
[Team Member 3] GET  /api/reports/bug/<id>   → Report rendered
```

---

## Bug Input Normalization

BugProof supports three modes of bug input.  All modes use the same `POST /api/bugs` endpoint:

| Input Mode | Fields used | Investigation focus |
|---|---|---|
| Description only | `description` | Keywords extracted from description text |
| With error message | `description` + `error_message` | Error identifiers prioritised; description used for breadth |
| With failing test | `description` + `extra_context.failing_test` | Test file resolved first; test function/module used as high-priority keywords |

If multiple inputs are provided, all are combined (error_message keywords take priority over description keywords).

### `failing_test` schema (inside `extra_context`)

```json
{
  "failing_test": {
    "file":     "tests/test_parser.py",
    "function": "test_tokenize_empty",
    "module":   "tests.test_parser"
  }
}
```

---

## Evidence vs Hypothesis – The Core Distinction

BugProof makes a hard, explicit distinction between two kinds of information.

### Machine-Verified Evidence (`is_verified = True`)

Facts confirmed by actual execution or file inspection:

- A specific file was read and a keyword was found at line N.
- A command was executed and produced this exact output.
- A test suite was run and N tests failed.
- A git patch was applied successfully to the repository.

These are represented as `Evidence` records with `is_verified = True`.

Additional fields on `Evidence` as of Member 1 milestone:
- `line_number_end`: end of the line range when evidence spans multiple lines.
- `search_query`: the keyword / search term that led to this evidence item.
- `relevance_explanation`: why this evidence is relevant to the bug.

### AI-Generated Content (`ai_generated = True`, `is_verified = False`)

Reasoning produced by an AI component that has **not** been machine-confirmed:

- Suspected root cause.
- Root cause explanation.
- Hypotheses about which component is at fault.
- Fix explanation and patch.

These are stored in `Investigation.suspected_cause`, `Investigation.root_cause_explanation`, `Hypothesis` records, and `Fix` records — all with `ai_generated = True`.

**A piece of AI-generated content is NEVER represented as verified evidence.**  
The `Investigation.root_cause_machine_supported` flag is only set to `True` when verified `Evidence` items exist that corroborate the AI's claim.

---

## Root Cause Analysis Status

The `RootCauseAnalysis` now carries an explicit `status` field:

| Status | Meaning |
|---|---|
| `confirmed_by_evidence` | Verified evidence exists AND AI confidence ≥ 0.7 |
| `suspected` | Verified evidence exists but AI confidence < 0.7, OR AI unavailable |
| `insufficient_evidence` | No verified evidence supports the suspected cause |

The `supporting_evidence_ids` field contains the database IDs of the `Evidence` records that support the root cause.

---

## Fix Proposal Fields

The `Fix` model as of Member 1 milestone includes:

| Field | Type | Description |
|---|---|---|
| `supporting_evidence_ids` | `JSON` | IDs of verified Evidence records that justify this fix |
| `affected_lines` | `JSON` | `[{file, line_start, line_end, description}]` from SOURCE_CODE evidence |
| `confidence` | `Float` | AI-reported confidence in the fix |

---

## Repository Abstraction

Because BugProof supports three target repositories, all repository-specific logic is isolated in adapter classes that implement `BaseRepository`.

### Interface (`BaseRepository`)

| Method | Description |
|---|---|
| `checkout(ref)` | Clone if needed; check out the given ref |
| `install_dependencies()` | Install the repository's runtime and test deps |
| `get_source_files()` | List Python source files relative to repo root |
| `get_test_spec()` | Return a `TestSpec` describing how to run tests |
| `run_tests(ref, extra_args)` | Run the test suite; return a result dict |
| `is_cloned()` | True if the repo dir exists on disk |

### Concrete Adapters

| Adapter | Repository | Test command |
|---|---|---|
| `TheFuckRepository` | github.com/nvbn/thefuck | `pytest tests/` |
| `TqdmRepository` | github.com/tqdm/tqdm | `pytest tests/` |
| `YoutubeDlRepository` | github.com/ytdl-org/youtube-dl | `python -m pytest test/` |

Use `get_repository(name, workspace_path)` to obtain an adapter by name.

---

## PostgreSQL Responsibility

PostgreSQL stores **metadata only**.  The actual source repositories are checked out to the filesystem workspace defined by `REPOS_WORKSPACE`.

### What PostgreSQL stores

| Table | Purpose |
|---|---|
| `bugs` | Bug reports; status; target repository reference |
| `investigations` | Investigation results; AI analysis; relevant files/functions |
| `hypotheses` | AI-generated hypotheses (explicitly marked) |
| `evidence` | Machine-verified evidence items (file, command, test) |
| `fixes` | AI-generated fix proposals; approval and application state |
| `test_runs` | Test execution metadata (command, output, exit code, counts) |
| `reports` | Debugging session summary reports |

### What PostgreSQL does NOT store

- The source repository content.
- Checked-out file trees.
- Large binary artefacts.

---

## Workflow / Run Management

A debugging run is long-running and must not block an HTTP request.

`DebuggingRun` tracks the in-memory state of a run with the following lifecycle:

```
RECEIVED → INVESTIGATING → ROOT_CAUSE_FOUND → FIX_GENERATED
         → WAITING_FOR_APPROVAL
         → FIX_APPLIED (after developer approval)
         → VERIFYING (Team Member 2)
         → COMPLETED
         → FAILED | CANCELLED
```

`WorkflowOrchestrator.execute()` is designed to run in a background task (e.g., Celery worker). It coordinates the pipeline steps and updates the database at each transition.

**The main debugging workflow is never executed as a single blocking HTTP request.**

---

## Fix Approval Gate

Fix proposals are AI-generated and require explicit developer approval before they can be applied.

```
AI generates patch  →  approval_status = PENDING
Developer reviews   →  GET /api/fixes/<id>
Developer approves  →  POST /api/fixes/<id>/approve  →  approval_status = APPROVED
Patch applied       →  POST /api/fixes/<id>/apply    →  application_status = APPLIED
```

A fix with `approval_status != APPROVED` cannot be applied. This is enforced in the API layer.

---

## Team Member 1 Ownership Boundaries

Team Member 1 owns and has implemented:

- Flask application factory (`backend/app.py`)
- Configuration system (`backend/config/`)
- PostgreSQL data models (`backend/models/`)
- Database connection layer (`backend/database/`)
- Repository abstraction and all three adapters (`backend/repositories/`)
- Execution layer – subprocess runner, git, test runner, sandbox (`backend/execution/`)
- AI service interfaces (`backend/ai/`)
- Core engine – investigation pipeline, root cause analyser, fix generator, workflow orchestrator (`backend/core/`)
- All five API Blueprint modules (`backend/api/`)
- Foundation test suite (`backend/tests/test_foundation.py`)
- Architecture documentation (`docs/ARCHITECTURE.md`)

---

## Integration Points for Future Team Members

### Team Member 2 – Verification / Testing

- **API entry point**: `POST /api/verification/run` (stub in `backend/api/verification.py`)
- **Data contract**: `TestRun` model in `backend/models/verification.py`
- **Execution primitives**: `TestRunner` in `backend/execution/tests.py`, `LocalSandbox` in `backend/execution/sandbox.py`
- **Workflow hook**: `WorkflowOrchestrator` transitions to `VERIFYING` status after fix application
- **Bug reproduction**: use `BaseRepository.run_tests(ref)` on the buggy ref

### Team Member 3 – Frontend / Reporting

- **API entry point**: `GET /api/reports/bug/<bug_id>` (stub in `backend/api/reports.py`)
- **Data contract**: `Report` model in `backend/models/verification.py`
- **Evidence payload**: all evidence items include `is_verified` flag for UI distinction
- **Hypothesis payload**: all hypotheses include `ai_generated = true` for UI labelling
- **Fix payload**: includes `approval_status`, `application_status`, `ai_generated` flags

---

## Security Notes

- All credentials are read from environment variables. No secrets are hardcoded.
- `.env` is excluded from git via `.gitignore`.
- `.env.example` contains only safe placeholder variable names.
- The `.bobignore` file prevents AI assistants from logging credentials.
- The approval gate for fix application prevents autonomous code modification.
