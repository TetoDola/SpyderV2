# Unforgetting — AI Pipeline Implementation Plan

> Personal CRM that passively builds a knowledge graph of everyone you know.
> Voice note / document dropped in → transcribed → entities extracted → graph updated → richer context next time.

---

## 1. Current State Audit

### What Exists & Can Be Reused

| Asset | Location | Reuse Potential |
|---|---|---|
| Node model with JSONField properties | `apps/network_graph/models.py` | Direct reuse. Already supports PERSON, COMPANY, MEETING with flexible properties. |
| Ghost node system | `apps/network_graph/models.py:38` | Direct reuse. `is_ghost=True` already exists for unconfirmed entities. Exactly what Step 3 needs. |
| @mention parser + connection diffing | `apps/network_graph/parser.py` | Reuse as-is for freeform notes. `sync_connections()` already does edge CRUD through mention parsing. |
| Connection model with relationship labels | `apps/network_graph/models.py:85-97` | Direct reuse. Source → Target with label. |
| Obsidian `[[Link]]` → `@[Link]` translator | `apps/network_graph/views.py:15` | Reuse. Already converts `[[Link]]` syntax to @mentions. |
| Import pipeline (markdown + frontmatter) | `apps/network_graph/views.py:248-348` | Partial reuse. Document import handles frontmatter extraction and ghost promotion. Needs to feed into the new pipeline instead of bypassing it. |
| System-locked property keys | `apps/network_graph/models.py:25-29` | Reuse. Email and Phone Number are already first-class on PERSON nodes — exactly the identifiers Step 3 needs for resolution. |
| Node search API | `apps/network_graph/views.py:184-198` | Reuse for Resolution Queue UI autocomplete. |
| Full graph JSON API | `apps/network_graph/views.py:25-51` | Reuse. Frontend graph already renders from this. |
| Frontend SPA (graph + peek modal) | `static/js/graph.js`, `templates/network_graph/index.html` | Reuse. UI exists. Pipeline outputs feed into existing node/edge display. |

### What Is Missing Entirely

| Gap | Why It's Needed |
|---|---|
| Async task runner (Celery/Django-Q) | Pipeline must run async. No task infrastructure exists. |
| LLM integration | No AI client anywhere. Needed for entity extraction (Step 2) and summarization (Step 5). |
| Audio transcription | No speech-to-text. Needed for voice notes (Step 1). |
| Document text extraction | No PDF/DOCX stripping. Only markdown import exists. |
| New node types: TOPIC, COMMITMENT, ACTION_ITEM | Only PERSON, COMPANY, MEETING exist. Pipeline extracts additional entity types. |
| Ingestion model (source tracking) | No concept of "an ingestion event" — need to track what was uploaded, what was extracted, what was written. |
| Resolution Queue model + API | Ghost nodes exist but there's no queue for user confirmation. |
| DSL/Cipher layer | Database mutations happen via direct ORM calls everywhere. No abstraction layer. |
| Summarization storage | No summary fields on nodes. Notes field exists but isn't structured for AI summaries. |
| Notification/alert system | Nothing for commitment reminders or review cards. |
| Environment file | No `.env` or `.env.example`. |
| Tests directory | Zero tests despite pytest being configured. |
| Celery config (broker, worker) | No `celery.py`, no broker URL, no task modules. |

### What Needs Modification

| File | Change Needed | Why |
|---|---|---|
| `apps/network_graph/models.py` | Add `TOPIC`, `COMMITMENT`, `ACTION_ITEM` to `NodeType` choices. Add `Ingestion`, `ResolutionCandidate` models. Add `summary` field to `Node`. | Pipeline extracts these entity types. Need ingestion tracking and resolution queue. |
| `apps/network_graph/parser.py` | Extract the `sync_connections` logic into the DSL layer. Parser stays, but writes go through DSL. | Critical rule: nothing touches DB directly. |
| `apps/network_graph/views.py` | Add ingestion upload endpoint. Modify `api_import_nodes` to route through pipeline. Add resolution queue endpoints. | New entry points for the pipeline. |
| `config/settings.py` | Add Celery config, LLM API keys from env, new app registrations. | Infrastructure for async + AI. |
| `pyproject.toml` | Add `celery`, `redis`, `openai`/`anthropic`, `python-docx`, `pdfplumber` dependencies. | New capabilities. |
| `templates/network_graph/index.html` | Add ingestion upload zone, review card panel, resolution queue modal. | UX for new pipeline inputs and outputs. |

---

## 2. Implementation Plan (Dependency-Ordered)

### Phase 0: Foundation

> No dependencies on other phases. Do this first.

**Task 0.1 — Environment files**
- Create `.env.example` with all required variables
- Create `.env` (gitignored) with dev defaults
- Update `settings.py` to use `django-environ`

**Task 0.2 — Expand NodeType choices**
- Add `TOPIC`, `COMMITMENT`, `ACTION_ITEM` to `NodeType`
- Add `SYSTEM_LOCKED_DEFAULTS` for new types:
  - `TOPIC`: `{"Topic Name": ""}`
  - `COMMITMENT`: `{"Due Date": "", "Assignee": "", "Status": "open"}`
  - `ACTION_ITEM`: `{"Due Date": "", "Assignee": "", "Status": "open"}`
- Migration

**Task 0.3 — Add `summary` TextField to Node**
- Running profile/summary field, separate from `notes`
- Migration

**Task 0.4 — Ingestion model**
- New model: `Ingestion`
  - `id`: UUID
  - `source_type`: CharField (`VOICE_NOTE`, `DOCUMENT`, `FREEFORM_NOTE`)
  - `original_file`: FileField (nullable, for uploaded files)
  - `raw_text`: TextField (transcribed/extracted text)
  - `extracted_json`: JSONField (Step 2 output)
  - `dsl_commands`: JSONField (Step 4 log)
  - `status`: CharField (`PENDING`, `TRANSCRIBING`, `EXTRACTING`, `RESOLVING`, `WRITING`, `SUMMARIZING`, `COMPLETE`, `FAILED`)
  - `error_message`: TextField (blank)
  - `created_at`, `completed_at`: DateTimeFields
- Migration

**Task 0.5 — Resolution Queue model**
- New model: `ResolutionCandidate`
  - `id`: UUID
  - `ingestion`: ForeignKey(Ingestion)
  - `extracted_name`: CharField
  - `extracted_email`: CharField (nullable)
  - `extracted_company`: CharField (nullable)
  - `extracted_title`: CharField (nullable)
  - `candidate_node`: ForeignKey(Node, nullable) — best-guess existing match
  - `confidence`: FloatField
  - `status`: CharField (`PENDING`, `CONFIRMED`, `REJECTED`, `AUTO_LINKED`)
  - `resolved_node`: ForeignKey(Node, nullable) — final linked node
  - `created_at`
- Migration

**Task 0.6 — Celery infrastructure**
- Create `config/celery.py` with app config
- Update `config/__init__.py` to load celery
- Add `CELERY_BROKER_URL` to settings (Redis)
- Create empty `apps/network_graph/tasks.py`

> **Decision needed**: Redis vs. a simpler broker? Redis is the standard choice and also useful as a cache later. Default is Redis unless otherwise specified.

---

### Phase 1: DSL Layer

> Depends on: Phase 0 (models exist)

**Task 1.1 — Graph DSL module**
- New file: `apps/network_graph/dsl.py`
- Four command functions:
  - `create_node(node_type, properties) -> Node`
  - `connect(node_a_id, node_b_id, relationship_label) -> Connection`
  - `update_profile(node_id, new_data) -> Node`
  - `flag_for_review(node_id, reason) -> ResolutionCandidate`
- Each command returns the object + logs itself to a list
- Accepts an `ingestion_id` context for audit logging
- All existing direct ORM writes (in `parser.py`, `views.py`) get rerouted through this layer

**Task 1.2 — Refactor parser.py to use DSL**
- `sync_connections()` calls `dsl.connect()` and `dsl.create_node()` instead of raw ORM
- Ghost node creation goes through `dsl.create_node()`
- No behavioral change, just routing

**Task 1.3 — Tests for DSL layer**
- `tests/test_dsl.py`: unit tests for each command
- `tests/test_parser.py`: existing parser logic still works through DSL

---

### Phase 2: Ingestion & Transcription (Step 1)

> Depends on: Phase 0 (Ingestion model, Celery)

**Task 2.1 — File upload endpoint**
- New view: `api_ingest(request)` — POST multipart
- Accepts: audio files (`.m4a`, `.mp3`, `.wav`, `.ogg`), documents (`.pdf`, `.docx`, `.txt`, `.md`), freeform text (JSON body with `text` field)
- Creates `Ingestion` record with `status=PENDING`
- Dispatches Celery task
- Returns `{ingestion_id, status}` immediately

**Task 2.2 — Transcription service**
- New file: `apps/network_graph/services/transcription.py`
- Wraps the existing TTS API
- Input: audio file path → Output: plain text
- Fallback: OpenAI Whisper API as backup

> **Decision needed**: What is the existing TTS API? Is it a local service, a cloud endpoint, or an SDK? Interface details needed.

**Task 2.3 — Document extraction service**
- New file: `apps/network_graph/services/document.py`
- PDF → `pdfplumber` → plain text
- DOCX → `python-docx` → plain text
- TXT/MD → read as-is (markdown already handled by existing import, but now routes through pipeline)
- Strips formatting, returns clean text

**Task 2.4 — Ingestion Celery task (Step 1 orchestrator)**
- In `tasks.py`: `process_ingestion(ingestion_id)`
- Routes to transcription or document extraction based on `source_type`
- Stores `raw_text` on Ingestion
- Updates status → `EXTRACTING`
- Chains to Step 2 task

---

### Phase 3: Entity Extraction (Step 2)

> Depends on: Phase 2 (raw_text available)

**Task 3.1 — LLM extraction service**
- New file: `apps/network_graph/services/extraction.py`
- Single LLM call with strict JSON schema
- Prompt template enforces output structure:

```json
{
  "people": [{"name": "", "email": null, "company": null, "title": null}],
  "companies": [{"name": "", "website": null}],
  "topics": [{"name": "", "context": ""}],
  "commitments": [{"description": "", "assignee": "", "due_date": null}],
  "action_items": [{"description": "", "assignee": "", "due_date": null}],
  "relationships": [{"from": "", "to": "", "label": ""}]
}
```

- Uses `response_format` / tool-use for guaranteed JSON (Anthropic or OpenAI)
- Stores result in `Ingestion.extracted_json`

> **Decision needed**: Anthropic Claude or OpenAI for the LLM calls? Recommendation: Claude (tool use with JSON schema) for quality, OpenAI (structured outputs) for cost. Or make it configurable behind an interface.

**Task 3.2 — Extraction Celery task**
- Calls extraction service
- Stores JSON result
- Updates status → `RESOLVING`
- Chains to Step 3

**Task 3.3 — Tests for extraction**
- Mock LLM responses
- Validate schema conformance
- Edge cases: no entities found, malformed text

---

### Phase 4: Entity Resolution (Step 3)

> Depends on: Phase 3 (extracted JSON), Phase 1 (DSL layer)

**Task 4.1 — Resolution service**
- New file: `apps/network_graph/services/resolution.py`
- For each extracted entity:
  1. **Email match** → `Node.objects.filter(properties__Email=email)` → auto-link, confidence=1.0
  2. **Phone match** → same pattern → auto-link, confidence=0.95
  3. **Exact title match** → `Node.objects.filter(title__iexact=name)` → auto-link, confidence=0.9
  4. **No match** → create `ResolutionCandidate` with `status=PENDING`, create ghost node via DSL
- Returns resolved entity list with node IDs and confidence scores
- **Never auto-merges on name similarity alone** — fuzzy matches go to queue

**Task 4.2 — Resolution Queue API**
- `GET /api/resolution-queue/` — list pending candidates
- `POST /api/resolution-queue/<id>/resolve/` — user confirms: link to existing node, create new, or dismiss
- `POST /api/resolution-queue/<id>/reject/` — user says "not the same person"
- When resolved: ghost node either merges into confirmed node or gets promoted

**Task 4.3 — Resolution Celery task**
- Runs resolution for all entities in extraction JSON
- Updates status → `WRITING`
- Chains to Step 4

**Task 4.4 — Tests for resolution**
- Email exact match
- Name-only → queue
- Ghost node creation
- Duplicate prevention

---

### Phase 5: Graph Writing (Step 4)

> Depends on: Phase 4 (resolved entities), Phase 1 (DSL)

**Task 5.1 — Graph writer service**
- New file: `apps/network_graph/services/graph_writer.py`
- Takes resolved entity list + extracted relationships
- Translates into DSL commands:
  - `CREATE_NODE` for new entities
  - `CONNECT` for relationships
  - `UPDATE_PROFILE` for existing nodes with new properties
  - `FLAG_FOR_REVIEW` for low-confidence matches
- Logs all commands to `Ingestion.dsl_commands`

**Task 5.2 — Graph writing Celery task**
- Executes DSL commands
- Updates status → `SUMMARIZING`
- Chains to Step 5

**Task 5.3 — Tests for graph writer**
- Correct node creation via DSL
- Correct edge creation
- Idempotency (re-running doesn't duplicate)

---

### Phase 6: Summarization (Step 5)

> Depends on: Phase 5 (graph written), Phase 3 (LLM service exists)

**Task 6.1 — Summarization service**
- New file: `apps/network_graph/services/summarization.py`
- Three summary types:
  1. **Per-ingestion**: 3 bullets + action items + commitments → stored on the ingestion's linked MEETING node (or a new one)
  2. **Per-person**: Fetch existing `Node.summary`, append new context, ask LLM to produce updated running profile → write to `Node.summary`
  3. **Per-company**: Aggregate all employee interaction summaries → write to company `Node.summary`
- Uses same LLM client as extraction

**Task 6.2 — Summarization Celery task**
- Runs all three summary types
- Updates status → `COMPLETE`, sets `completed_at`

**Task 6.3 — Tests for summarization**
- Mock LLM, verify summary format
- Cumulative profile updates don't lose data

---

### Phase 7: Notifications (Step 6)

> Depends on: Phase 5 (graph written), Phase 6 (summaries done)

**Task 7.1 — Review card endpoint**
- `GET /api/ingestions/<id>/review/` — returns what was extracted, what was written, what needs confirmation
- Frontend renders this as a toast/card after ingestion completes

**Task 7.2 — Commitment scheduling**
- When `ACTION_ITEM` or `COMMITMENT` nodes are created with due dates → create a scheduled reminder
- Simple approach: `ScheduledReminder` model with `due_at` field + a periodic Celery beat task that checks for due items
- Returns reminders via API for frontend display

**Task 7.3 — Resolution Queue alerts**
- `GET /api/resolution-queue/count/` — returns pending count
- Frontend polls this (or uses SSE later) to show badge on queue button

**Task 7.4 — Frontend integration**
- Add ingestion upload zone to `templates/network_graph/index.html` (drag-drop area or button)
- Add review card panel (slide-in after ingestion completes)
- Add resolution queue modal (list of pending candidates with confirm/reject)
- Add reminder indicator

---

## 3. File-by-File Changes

### New Files to Create

| File | Purpose |
|---|---|
| `.env.example` | Template for all environment variables |
| `.env` | Local dev config (gitignored) |
| `config/celery.py` | Celery app configuration |
| `apps/network_graph/dsl.py` | Graph mutation DSL layer |
| `apps/network_graph/tasks.py` | All Celery task definitions |
| `apps/network_graph/services/__init__.py` | Services package |
| `apps/network_graph/services/transcription.py` | Audio → text |
| `apps/network_graph/services/document.py` | PDF/DOCX → text |
| `apps/network_graph/services/extraction.py` | LLM entity extraction |
| `apps/network_graph/services/resolution.py` | Entity resolution logic |
| `apps/network_graph/services/graph_writer.py` | DSL command execution |
| `apps/network_graph/services/summarization.py` | LLM summarization |
| `tests/__init__.py` | Test package |
| `tests/conftest.py` | Shared fixtures, factories |
| `tests/test_dsl.py` | DSL unit tests |
| `tests/test_parser.py` | Parser regression tests |
| `tests/test_extraction.py` | Extraction service tests |
| `tests/test_resolution.py` | Resolution service tests |
| `tests/test_graph_writer.py` | Graph writer tests |
| `tests/test_summarization.py` | Summarization tests |
| `tests/test_tasks.py` | End-to-end pipeline task tests |
| `tests/factories.py` | Factory Boy factories for Node, Connection, Ingestion |

### Existing Files to Modify

| File | Changes |
|---|---|
| `config/settings.py` | Add `django-environ` loading, Celery settings (`CELERY_BROKER_URL`, `CELERY_RESULT_BACKEND`), `LLM_PROVIDER`, `LLM_API_KEY`, `TRANSCRIPTION_API_URL` env vars |
| `config/__init__.py` | Import celery app for autodiscovery |
| `apps/network_graph/models.py` | Add `TOPIC`, `COMMITMENT`, `ACTION_ITEM` to NodeType. Add `summary` field. Add `Ingestion` model. Add `ResolutionCandidate` model. Add `ScheduledReminder` model. |
| `apps/network_graph/parser.py` | Replace direct ORM calls in `sync_connections()` with `dsl.create_node()` and `dsl.connect()`. Keep parsing logic untouched. |
| `apps/network_graph/views.py` | Add `api_ingest()` endpoint. Add resolution queue endpoints. Add ingestion review endpoint. Add reminder endpoints. Refactor `api_import_nodes` to optionally route through pipeline. |
| `apps/network_graph/urls.py` | Add URL patterns for new endpoints |
| `apps/network_graph/admin.py` | Register `Ingestion`, `ResolutionCandidate`, `ScheduledReminder` |
| `pyproject.toml` | Add `celery`, `redis`, `anthropic` (or `openai`), `python-docx`, `pdfplumber`, `django-environ` |
| `templates/network_graph/index.html` | Add upload zone, review card, resolution queue modal, reminder badge |
| `static/js/graph.js` | Add ingestion upload handler, review card rendering, resolution queue UI, polling for queue count |
| `.gitignore` | Ensure `.env` is listed (likely already is) |

---

## 4. Dependencies & Services

### New Python Packages

| Package | Purpose |
|---|---|
| `celery[redis]>=5.4` | Async task queue |
| `redis>=5.0` | Celery broker + result backend |
| `anthropic>=0.40` | LLM API client (entity extraction + summarization) |
| `python-docx>=1.1` | DOCX text extraction |
| `pdfplumber>=0.11` | PDF text extraction |
| `django-environ>=0.11` | `.env` file loading |

### External Services Required

| Service | Purpose | Notes |
|---|---|---|
| Redis | Celery broker | Local install or Docker container for dev |
| Anthropic API (or OpenAI) | Entity extraction + summarization | Needs API key. ~2 LLM calls per ingestion. |
| Existing TTS API | Voice transcription | Endpoint details needed |

### Environment Variables

```bash
# .env.example
DJANGO_SECRET_KEY=change-me-in-production
DJANGO_DEBUG=true

# Database (SQLite default for dev)
DB_ENGINE=django.db.backends.sqlite3
DB_NAME=db.sqlite3
DB_USER=
DB_PASSWORD=
DB_HOST=
DB_PORT=

# Celery
CELERY_BROKER_URL=redis://localhost:6379/0
CELERY_RESULT_BACKEND=redis://localhost:6379/0

# LLM
LLM_PROVIDER=anthropic
ANTHROPIC_API_KEY=sk-ant-...
# OPENAI_API_KEY=sk-...

# Transcription
TRANSCRIPTION_API_URL=http://localhost:8001/transcribe
TRANSCRIPTION_API_KEY=
```

---

## 5. Risk Flags

### Schema Changes Requiring Migrations

| Change | Risk | Mitigation |
|---|---|---|
| Adding 3 new NodeType choices | **Low**. TextChoices expansion is additive. Existing rows unaffected. | Straightforward migration. |
| Adding `summary` TextField to Node | **Low**. Nullable/blank field, no data loss. | `blank=True, default=""` |
| New `Ingestion` model | **Low**. New table, no FK changes to existing models. | Clean migration. |
| New `ResolutionCandidate` model | **Low**. New table with FKs to Node and Ingestion. | Clean migration. |
| New `ScheduledReminder` model | **Low**. New table. | Clean migration. |

### Architectural Conflicts

| Issue | Severity | Recommendation |
|---|---|---|
| `parser.py` bypasses DSL — `sync_connections()` does direct ORM writes | **Medium**. Must be refactored before pipeline goes live or you'll have two code paths for graph mutation. | Phase 1 Task 1.2 handles this. Do it early. |
| `api_import_nodes` bypasses pipeline — Markdown import currently does its own entity creation inline | **Medium**. Once pipeline exists, imported markdown should route through Steps 2-6 for AI extraction, not just frontmatter parsing. | Add a flag: `use_pipeline=True` routes through Celery tasks, `use_pipeline=False` keeps current fast-path for bulk imports. |
| SQLite in production | **High if you scale**. JSONField queries for email matching (`properties__Email=email`) work on SQLite but are fragile. PostgreSQL has proper JSON operators. | Move to PostgreSQL before pipeline goes live. The `psycopg` dependency is already in `pyproject.toml`. |
| No authentication | **High**. Pipeline creates data, sends notifications, has admin-level operations. No auth means anyone can trigger ingestions. | Out of scope per brief, but flag it: add auth before any deployment. |
| CSRF exempt on all write endpoints | **Medium**. Fine for local dev, dangerous in production. | Address with auth layer. Not blocking for pipeline work. |
| `Any` type in `models.py:48` | **Low**. `save(*args: Any, **kwargs: Any)` violates the "no Any types" rule. | Fix when touching the file. Use `object` or proper Django types. |

### Hard-to-Reverse Decisions

| Decision | Why It Matters |
|---|---|
| DSL as a Python function layer vs. an actual command log | If you want replay/undo, the DSL needs to be a serializable command log stored on Ingestion, not just function wrappers. The plan proposes the log approach (`Ingestion.dsl_commands` stores the full command history). This is the right call but means the DSL is slightly more complex. |
| LLM provider choice | Switching between Anthropic and OpenAI later is doable (same interface pattern) but the prompt engineering is different. Pick one now, make it swappable via interface. |
| Summary as a separate field vs. appending to notes | The plan proposes `Node.summary` as a dedicated field, keeping `notes` for user-written content. This is cleaner but means the frontend needs to display both. Alternative: structured section within `notes` — but that's fragile and harder to update programmatically. |

---

## Decisions Needed Before Implementation

1. **LLM provider**: Anthropic Claude or OpenAI? (Or both behind an interface?)
2. **Existing TTS API**: What's the endpoint/SDK? Interface details needed for wrapping.
3. **Summary field vs. notes section**: Separate `summary` field on Node (recommended) or structured section within existing `notes`?
