# Unforgetting — AI Pipeline Implementation Plan (MVP-Scoped)

> Personal CRM that passively builds a knowledge graph of everyone you know.
> Voice note / document dropped in → transcribed → entities extracted → graph updated → richer context next time.

**MVP Scope**: PERSON, COMPANY, and MEETING nodes. Three separate ingestion paths (voice notes, documents, freeform notes). Three-level summaries (per-meeting, per-person, per-company).

---

## 1. Current State Audit

### What Exists & Can Be Reused

| Asset | Location | Reuse Potential |
|---|---|---|
| Node model with JSONField properties | `apps/network_graph/models.py` | Direct reuse. Already supports PERSON, COMPANY, MEETING with flexible properties. |
| Ghost node system | `apps/network_graph/models.py:38` | Direct reuse. `is_ghost=True` already exists for unconfirmed entities. Exactly what entity resolution needs. |
| @mention parser + connection diffing | `apps/network_graph/parser.py` | Reuse as-is for freeform notes. `sync_connections()` already does edge CRUD through mention parsing. |
| Connection model with relationship labels | `apps/network_graph/models.py:85-97` | Direct reuse. Source → Target with label. |
| Obsidian `[[Link]]` → `@[Link]` translator | `apps/network_graph/views.py:15` | Reuse. Already converts `[[Link]]` syntax to @mentions. |
| Import pipeline (markdown + frontmatter) | `apps/network_graph/views.py:248-348` | Partial reuse. Document import handles frontmatter extraction and ghost promotion. Needs to feed into the new pipeline instead of bypassing it. |
| System-locked property keys | `apps/network_graph/models.py:25-29` | Reuse. Email and Phone Number are already first-class on PERSON nodes — exactly the identifiers entity resolution needs. |
| Node search API | `apps/network_graph/views.py:184-198` | Reuse for Resolution Queue UI autocomplete. |
| Full graph JSON API | `apps/network_graph/views.py:25-51` | Reuse. Frontend graph already renders from this. |
| Frontend SPA (graph + peek modal) | `static/js/graph.js`, `templates/network_graph/index.html` | Reuse. UI exists. Pipeline outputs feed into existing node/edge display. |

### What Is Missing Entirely

| Gap | Why It's Needed |
|---|---|
| Async task runner (Celery/Django-Q) | Pipeline must run async. No task infrastructure exists. |
| LLM integration | No AI client anywhere. Needed for entity extraction and summarization. |
| Audio transcription | No speech-to-text. Needed for voice notes. |
| Document text extraction | No PDF/DOCX stripping. Only markdown import exists. |
| Ingestion model (source tracking) | No concept of "an ingestion event" — need to track what was uploaded, extracted, and written. |
| Resolution Queue model + API | Ghost nodes exist but there's no queue for user confirmation. |
| DSL/Cipher layer | Database mutations happen via direct ORM calls everywhere. No abstraction layer. |
| Summarization storage | No summary fields on nodes. Notes field exists but isn't structured for AI summaries. |
| Graph schema contract | No specification of what a PERSON, COMPANY, or MEETING node must contain. Silent data loss risk between extraction and storage. |
| Environment file | No `.env` or `.env.example`. |
| Tests directory | Zero tests despite pytest being configured. |
| Celery config (broker, worker) | No `celery.py`, no broker URL, no task modules. |

### What Needs Modification

| File | Change Needed | Why |
|---|---|---|
| `apps/network_graph/models.py` | Add `Ingestion`, `ResolutionCandidate` models. Add `summary` JSONField to `Node`. | Need ingestion tracking, resolution queue, and structured summaries. |
| `apps/network_graph/parser.py` | Extract the `sync_connections` logic into the DSL layer. Parser stays, but writes go through DSL. | Critical rule: nothing touches DB directly. |
| `apps/network_graph/views.py` | Add three separate ingestion endpoints (voice, document, freeform). Modify `api_import_nodes` to route through pipeline. Add resolution queue endpoints. | New entry points for the pipeline — one per source type. |
| `config/settings.py` | Add Celery config, LLM API keys from env, new app registrations. | Infrastructure for async + AI. |
| `pyproject.toml` | Add `celery`, `redis`, `anthropic`/`openai`, `python-docx`, `pdfplumber` dependencies. | New capabilities. |
| `templates/network_graph/index.html` | Add ingestion upload zones, review card panel, resolution queue modal. | UX for new pipeline inputs and outputs. |

---

## 2. Implementation Plan (Dependency-Ordered)

### Phase 0: Foundation

> No dependencies on other phases. Do this first.

**Task 0.0 — PostgreSQL migration**
- Celery + SQLite = database-locked errors. Switch to PostgreSQL first.
- The `psycopg` dependency is already in `pyproject.toml`.

**Task 0.1 — Environment files**
- Create `.env.example` with all required variables
- Create `.env` (gitignored) with dev defaults
- Update `settings.py` to use `django-environ`

**Task 0.2 — Node types: PERSON, COMPANY, MEETING**
- All three existing node types stay. No new types needed for MVP.
- COMPANY is a first-class node — extracted companies create COMPANY nodes, linked to PERSON nodes via WORKS_AT edges.

**Task 0.3 — Add `summary` JSONField to Node**
- Structured JSON summary field, separate from `notes` (user-written content)
- Migration

**Task 0.4 — Ingestion model**
- New model: `Ingestion`
  - `id`: UUID
  - `source_type`: CharField (`VOICE_NOTE`, `DOCUMENT`, `FREEFORM_NOTE`)
  - `original_file`: FileField (nullable, for uploaded files)
  - `raw_text`: TextField (transcribed/extracted text)
  - `extracted_json`: JSONField (extraction output)
  - `dsl_commands`: JSONField (graph writing log)
  - `status`: CharField (`PENDING`, `TRANSCRIBING`, `EXTRACTING`, `RESOLVING`, `WRITING`, `SUMMARIZING`, `COMPLETE`, `FAILED`)
  - `error_message`: TextField (blank)
  - `failed_step`: CharField (nullable — which step failed, for retry-from)
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

**Task 0.7 — Seed import (cold start solution)**
- **Contacts CSV import** — Google Contacts or LinkedIn export. Parse name, email, company, title. Creates PERSON nodes + COMPANY nodes via DSL. Gives 50-500 nodes immediately.
- **Bulk note import** — existing Obsidian `[[Link]]` translator. Dump markdown notes, run through pipeline.
- **Manual quick-add** — fast form: name, email, one-line context. Creates PERSON node instantly.
- New endpoint: `POST /api/import/contacts/` (accepts CSV)
- New service: `apps/network_graph/services/contacts_import.py`

**Task 0.8 — Graph schema definition**
- New file: `apps/network_graph/schema.py`
- Defines what PERSON, COMPANY, and MEETING nodes must contain:

```
PERSON node properties:
  System-locked (existing):
    - First Name
    - Last Name
    - Email           — canonical identifier
    - Phone Number    — secondary identifier
  Pipeline-populated:
    - Title           — job title, extracted from context
    - First Met       — date of earliest ingestion mentioning them
    - Last Interaction — date of most recent ingestion
    - Interaction Count — total ingestions mentioning them

COMPANY node properties:
  System-locked (existing):
    - Company Name
    - Website
    - Phone Number
  Pipeline-populated:
    - Industry        — extracted from context if mentioned
    - Employee Count  — number of PERSON nodes linked via WORKS_AT

MEETING node properties:
  System-locked (existing):
    - Date
    - Attendees
  Pipeline-populated:
    - Source Type      — voice_note / document / freeform_note
    - Participants     — list of PERSON node IDs (also captured as edges)
    - Key Points       — from meeting summary
    - Decisions        — from meeting summary
    - Follow Ups       — from meeting summary

Edge types:
  - ATTENDED    (person → meeting)
  - KNOWS       (person ↔ person, with context label)
  - WORKS_AT    (person → company)
  - DISCUSSED   (meeting → company, when company is discussed in a meeting)
```

- DSL `create_node()` validates properties against schema
- Extraction prompt references exact field names from schema

---

### Phase 1: DSL Layer

> Depends on: Phase 0 (models exist)

**Task 1.1 — Graph DSL module (5 commands)**
- New file: `apps/network_graph/dsl.py`
- Five command functions:
  - `create_node(node_type, properties) -> Node`
  - `connect(node_a_id, node_b_id, relationship_label) -> Connection`
  - `update_profile(node_id, new_data) -> Node`
  - `flag_for_review(node_id, reason) -> ResolutionCandidate`
  - `merge_nodes(source_id, target_id) -> Node` — transfers all connections from source to target, merges properties (target wins on conflicts), appends source summary to target summary, deletes source node, logs merge
- Each command returns the object + logs itself to a list
- Accepts an `ingestion_id` context for audit logging
- All existing direct ORM writes (in `parser.py`, `views.py`) get rerouted through this layer

**Task 1.2 — Refactor parser.py to use DSL**
- `sync_connections()` calls `dsl.connect()` and `dsl.create_node()` instead of raw ORM
- Ghost node creation goes through `dsl.create_node()`
- No behavioral change, just routing

**Task 1.3 — Tests for DSL layer**
- `tests/test_dsl.py`: unit tests for each command (including merge)
- `tests/test_parser.py`: existing parser logic still works through DSL

---

### Phase 2: Ingestion & Transcription (Step 1)

> Depends on: Phase 0 (Ingestion model, Celery)
>
> **Each source type has its own endpoint, service, and Celery task.** They share the downstream pipeline (extraction → resolution → writing → summarization) but the ingestion step is fully separated.

**Task 2.1 — Voice note ingestion**
- New endpoint: `POST /api/ingest/voice/` — accepts audio upload (`.m4a`, `.mp3`, `.wav`, `.ogg`)
- Creates `Ingestion` record with `source_type=VOICE_NOTE`, `status=PENDING`
- Dispatches `process_voice_note` Celery task
- Returns `{ingestion_id, status}` immediately
- New service: `apps/network_graph/services/ingest_voice.py`
  - Wraps the existing TTS API for transcription
  - Input: audio file path → Output: plain text
  - Fallback: OpenAI Whisper API as backup
  - Stores `raw_text` on Ingestion, updates status → `EXTRACTING`
- Celery task: `process_voice_note(ingestion_id)`
  - Saves audio file to media
  - Calls transcription service
  - Chains to shared extraction task
  - Retry: 3x, exponential backoff (network call)

**Task 2.2 — Document ingestion**
- New endpoint: `POST /api/ingest/document/` — accepts file upload (`.pdf`, `.docx`, `.txt`, `.md`)
- Creates `Ingestion` record with `source_type=DOCUMENT`, `status=PENDING`
- Dispatches `process_document` Celery task
- Returns `{ingestion_id, status}` immediately
- New service: `apps/network_graph/services/ingest_document.py`
  - PDF → `pdfplumber` → plain text
  - DOCX → `python-docx` → plain text
  - TXT → read as-is
  - MD → translate `[[Link]]` to `@[Link]`, strip image embeds, pass through
  - Stores `raw_text` on Ingestion, updates status → `EXTRACTING`
- Celery task: `process_document(ingestion_id)`
  - Saves file to media
  - Calls document extraction service
  - Chains to shared extraction task
  - Retry: 1x (local I/O, unlikely to fail)

**Task 2.3 — Freeform note ingestion**
- New endpoint: `POST /api/ingest/note/` — accepts JSON body `{"text": "...", "title": ""}`
- Creates `Ingestion` record with `source_type=FREEFORM_NOTE`, `status=PENDING`
- Dispatches `process_freeform_note` Celery task
- Returns `{ingestion_id, status}` immediately
- New service: `apps/network_graph/services/ingest_freeform.py`
  - Translates `[[Link]]` to `@[Link]` syntax
  - Passes text through as-is (no extraction needed)
  - Stores `raw_text` on Ingestion, updates status → `EXTRACTING`
- Celery task: `process_freeform_note(ingestion_id)`
  - No file to save — text comes from request body
  - Calls freeform service (lightweight transform)
  - Chains to shared extraction task
  - No retry needed (no I/O)

**Task 2.4 — Tests for ingestion**
- `tests/test_ingest_voice.py`: transcription service mock, audio file handling
- `tests/test_ingest_document.py`: PDF/DOCX/TXT/MD extraction
- `tests/test_ingest_freeform.py`: text passthrough, `[[Link]]` translation

---

### Phase 3: Entity Extraction (Step 2)

> Depends on: Phase 2 (raw_text available from any source)
>
> **Shared step** — all three ingestion paths converge here. Same Celery task regardless of source type.

**Task 3.1 — LLM extraction service (people, companies, relationships)**
- New file: `apps/network_graph/services/extraction.py`
- Single LLM call with strict JSON schema
- MVP extraction schema:

```json
{
  "people": [
    {"name": "", "email": null, "company": null, "title": null}
  ],
  "companies": [
    {"name": "", "website": null, "industry": null}
  ],
  "relationships": [
    {"from": "", "to": "", "label": ""}
  ],
  "meeting_context": {
    "date": null,
    "key_points": [],
    "decisions": []
  }
}
```

- People carry company and title as identifiers — these also inform COMPANY node creation
- Companies extracted as first-class entities → become COMPANY nodes
- Relationships capture edges between any entity types (KNOWS, WORKS_AT, ATTENDED, DISCUSSED)
- `meeting_context` feeds directly into the MEETING node and its summary
- Uses `response_format` / tool-use for guaranteed JSON
- Stores result in `Ingestion.extracted_json`
- Retry: 2x on malformed JSON

**Task 3.2 — Extraction Celery task**
- `extract_entities(ingestion_id)` — shared task called by all three ingestion paths
- Calls extraction service
- Stores JSON result
- Updates status → `RESOLVING`
- Chains to resolution task

**Task 3.3 — Tests for extraction**
- Mock LLM responses
- Validate schema conformance
- Edge cases: no entities found, malformed text, company mentioned without people

---

### Phase 4: Entity Resolution (Step 3)

> Depends on: Phase 3 (extracted JSON), Phase 1 (DSL layer)

**Task 4.1 — Resolution service (revised cascade, no name auto-link)**
- New file: `apps/network_graph/services/resolution.py`
- Resolves both PERSON and COMPANY entities:

**PERSON resolution cascade:**
```
1. Email exact match              → auto-link (confidence 1.0)
2. Phone exact match              → auto-link (confidence 0.95)
3. Name exact + same company prop → queue for confirmation (confidence 0.7)
4. Name exact, no company context → queue for confirmation (confidence 0.5)
5. No match                       → create ghost PERSON node via DSL
```

**COMPANY resolution cascade:**
```
1. Name exact match (case-insensitive) → auto-link (confidence 1.0)
2. Website exact match                  → auto-link (confidence 0.95)
3. No match                             → create COMPANY node via DSL (not ghost — companies are lower risk to auto-create)
```

- Rule: only confidence >= 0.9 auto-links for PERSON. Everything else goes to the queue.
- Companies auto-create more aggressively (exact name match is sufficient) since merging companies is lower risk than merging people.
- Returns resolved entity list with node IDs and confidence scores
- **Never auto-merges people on name similarity alone**
- Retry: 1x (local logic, unlikely to fail)

**Task 4.2 — Resolution Queue API**
- `GET /api/resolution-queue/` — list pending candidates
- `POST /api/resolution-queue/<id>/resolve/` — user confirms: link to existing node (triggers `dsl.merge_nodes()`), create new, or dismiss
- `POST /api/resolution-queue/<id>/reject/` — user says "not the same person"
- When resolved: ghost node either merges into confirmed node or gets promoted

**Task 4.3 — Resolution Celery task**
- `resolve_entities(ingestion_id)` — shared task
- Runs resolution for all people and companies in extraction JSON
- Updates status → `WRITING`
- Chains to graph writing task

**Task 4.4 — Tests for resolution**
- Email exact match → auto-link
- Name-only → queue (never auto-link)
- Name + same company → queue with 0.7 confidence
- Company exact name → auto-link
- Ghost node creation (PERSON only — companies create real nodes)
- Duplicate prevention

---

### Phase 5: Graph Writing (Step 4)

> Depends on: Phase 4 (resolved entities), Phase 1 (DSL)

**Task 5.1 — Graph writer service**
- New file: `apps/network_graph/services/graph_writer.py`
- Takes resolved entity list + extracted relationships
- Translates into DSL commands:
  - `CREATE_NODE` for new PERSON, COMPANY, and MEETING entities
  - `CONNECT` for relationships:
    - `ATTENDED` — person → meeting
    - `KNOWS` — person ↔ person (with context label)
    - `WORKS_AT` — person → company (from extracted company property)
    - `DISCUSSED` — meeting → company (when company is mentioned in context)
  - `UPDATE_PROFILE` for existing nodes with new properties (Title, Last Interaction, Interaction Count)
  - `FLAG_FOR_REVIEW` for low-confidence matches
- Logs all commands to `Ingestion.dsl_commands`
- Retry: 1x (local DB)

**Task 5.2 — Graph writing Celery task**
- `write_graph(ingestion_id)` — shared task
- Executes DSL commands
- Updates status → `SUMMARIZING`
- Chains to summarization task

**Task 5.3 — Tests for graph writer**
- Correct node creation via DSL (PERSON, COMPANY, MEETING)
- Correct edge creation (ATTENDED, KNOWS, WORKS_AT, DISCUSSED)
- Idempotency (re-running doesn't duplicate)

---

### Phase 6: Summarization (Step 5)

> Depends on: Phase 5 (graph written), Phase 3 (LLM service exists)

**Task 6.1a — Per-meeting summary (structured output)**
- LLM receives: raw text + resolved PERSON nodes with their existing summaries (so it knows who these people are in context)
- Output structure:

```json
{
  "one_liner": "Discussed Series A timeline with Sarah",
  "key_points": [
    "Sarah wants to close by Q3",
    "They're looking for a lead investor"
  ],
  "decisions": ["Agreed to intro Sarah to James at Sequoia"],
  "follow_ups": ["Send intro email by Friday"]
}
```

- Follow-ups and decisions are **fields on the MEETING node**, not separate entity types
- Stored in `Node.summary` as structured JSON

**Task 6.1b — Per-person running profile (append-rewrite)**
- Append-and-rewrite pattern: fetch existing summary → append new interaction context → LLM consolidates into updated profile
- Profile structure:

```json
{
  "role": "CTO at Acme Corp",
  "how_we_know_each_other": "Met at YC Demo Day 2024",
  "last_interaction": "2025-03-01",
  "key_context": [
    "Raising Series A, wants to close Q3",
    "Looking for senior engineers"
  ],
  "follow_ups_involving_them": ["Send intro to James"]
}
```

- **Max length cap** (500 words) to prevent profiles from bloating after 30+ interactions. LLM must compress, not append forever.
- Stored in `Node.summary` as structured JSON

**Task 6.1c — Per-company health summary**
- Aggregates all employee interaction summaries for a given COMPANY node
- LLM receives: all PERSON summaries linked via WORKS_AT + all MEETING summaries involving those people
- Output structure:

```json
{
  "company_name": "Acme Corp",
  "relationship_health": "strong",
  "total_contacts": 3,
  "last_interaction": "2025-03-01",
  "key_context": [
    "Raising Series A",
    "3 contacts across engineering and leadership"
  ],
  "open_follow_ups": ["Intro Sarah to James at Sequoia"]
}
```

- **Max length cap** (300 words) — company summaries should be scannable at a glance
- Only regenerated when a linked PERSON's summary changes (not on every ingestion)
- Stored in COMPANY `Node.summary` as structured JSON

**Task 6.2 — Summarization Celery task**
- `summarize(ingestion_id)` — shared task
- Runs per-meeting, then per-person, then per-company summaries (in order — each depends on the previous)
- Updates status → `COMPLETE`, sets `completed_at`
- Retry: 2x (LLM call)

**Task 6.3 — Tests for summarization**
- Mock LLM, verify summary format matches schema for all three types
- Cumulative person profile updates don't lose data
- Company summary aggregates correctly from linked people
- Max length caps are respected

---

### Phase 7: Frontend & Review

> Depends on: Phase 5 (graph written), Phase 6 (summaries done)

**Task 7.1 — Review card endpoint**
- `GET /api/ingestions/<id>/review/` — returns what was extracted, what was written, what needs confirmation
- Frontend renders this as a toast/card after ingestion completes
- Failed ingestions show which step failed + retry button
- New endpoint: `POST /api/ingestions/<id>/retry/` — retries from last failed step

**Task 7.2 — Frontend integration**
- Add three ingestion upload zones to `templates/network_graph/index.html`:
  - Voice note recorder/upload button → calls `POST /api/ingest/voice/`
  - Document drop zone → calls `POST /api/ingest/document/`
  - Freeform note text area → calls `POST /api/ingest/note/`
- Add review card panel (slide-in after ingestion completes)
- Add resolution queue modal (list of pending candidates with confirm/reject)

---

## 3. File-by-File Changes

### New Files to Create

| File | Purpose |
|---|---|
| `.env.example` | Template for all environment variables |
| `.env` | Local dev config (gitignored) |
| `config/celery.py` | Celery app configuration |
| `apps/network_graph/dsl.py` | Graph mutation DSL layer (5 commands incl. merge_nodes) |
| `apps/network_graph/schema.py` | Graph schema contract for PERSON, COMPANY, MEETING nodes |
| `apps/network_graph/tasks.py` | All Celery task definitions with retry policies |
| `apps/network_graph/services/__init__.py` | Services package |
| `apps/network_graph/services/ingest_voice.py` | Voice note transcription (audio → text) |
| `apps/network_graph/services/ingest_document.py` | Document extraction (PDF/DOCX/TXT/MD → text) |
| `apps/network_graph/services/ingest_freeform.py` | Freeform note passthrough (text → text) |
| `apps/network_graph/services/extraction.py` | LLM entity extraction (people, companies, relationships) |
| `apps/network_graph/services/resolution.py` | Entity resolution with separate PERSON/COMPANY cascades |
| `apps/network_graph/services/graph_writer.py` | DSL command execution |
| `apps/network_graph/services/summarization.py` | Per-meeting, per-person, per-company summaries |
| `apps/network_graph/services/contacts_import.py` | CSV contacts import (seed data) |
| `apps/network_graph/prompts/` | LLM prompt templates directory |
| `tests/__init__.py` | Test package |
| `tests/conftest.py` | Shared fixtures, factories |
| `tests/factories.py` | Factory Boy factories for Node, Connection, Ingestion |
| `tests/test_dsl.py` | DSL unit tests (incl. merge_nodes) |
| `tests/test_parser.py` | Parser regression tests |
| `tests/test_ingest_voice.py` | Voice note ingestion tests |
| `tests/test_ingest_document.py` | Document ingestion tests |
| `tests/test_ingest_freeform.py` | Freeform note ingestion tests |
| `tests/test_extraction.py` | Extraction service tests |
| `tests/test_resolution.py` | Resolution service tests |
| `tests/test_graph_writer.py` | Graph writer tests |
| `tests/test_summarization.py` | Summarization tests |
| `tests/test_tasks.py` | End-to-end pipeline task tests |

### Existing Files to Modify

| File | Changes |
|---|---|
| `config/settings.py` | Add `django-environ` loading, Celery settings (`CELERY_BROKER_URL`, `CELERY_RESULT_BACKEND`), `LLM_PROVIDER`, `LLM_API_KEY`, `TRANSCRIPTION_API_URL` env vars, PostgreSQL as default |
| `config/__init__.py` | Import celery app for autodiscovery |
| `apps/network_graph/models.py` | Add `summary` JSONField to Node. Add `Ingestion` model (with `failed_step`). Add `ResolutionCandidate` model. |
| `apps/network_graph/parser.py` | Replace direct ORM calls in `sync_connections()` with `dsl.create_node()` and `dsl.connect()`. Keep parsing logic untouched. |
| `apps/network_graph/views.py` | Add three ingestion endpoints (`api_ingest_voice`, `api_ingest_document`, `api_ingest_note`). Add resolution queue endpoints. Add ingestion review + retry endpoints. Add contacts import endpoint. Refactor `api_import_nodes` to optionally route through pipeline. |
| `apps/network_graph/urls.py` | Add URL patterns for all new endpoints |
| `apps/network_graph/admin.py` | Register `Ingestion`, `ResolutionCandidate` |
| `pyproject.toml` | Add `celery[redis]`, `redis`, `anthropic` (or `openai`), `python-docx`, `pdfplumber`, `django-environ` |
| `templates/network_graph/index.html` | Add three upload zones (voice, document, freeform), review card, resolution queue modal |
| `static/js/graph.js` | Add three ingestion handlers, review card rendering, resolution queue UI |
| `.gitignore` | Ensure `.env` is listed |

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
| PostgreSQL | Primary database | Required — SQLite + Celery = lock errors |
| Redis | Celery broker | Local install or Docker container for dev |
| Anthropic API (or OpenAI) | Entity extraction + summarization | Needs API key. ~3 LLM calls per ingestion (extraction + meeting summary + person profiles). |
| Existing TTS API | Voice transcription | Endpoint details needed |

### Environment Variables

```bash
# .env.example
DJANGO_SECRET_KEY=change-me-in-production
DJANGO_DEBUG=true

# Database (PostgreSQL for pipeline)
DB_ENGINE=django.db.backends.postgresql
DB_NAME=unforgetting
DB_USER=postgres
DB_PASSWORD=
DB_HOST=localhost
DB_PORT=5432

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
| Adding `summary` JSONField to Node | **Low**. Nullable/blank field, no data loss. | `blank=True, default=dict` |
| New `Ingestion` model | **Low**. New table, no FK changes to existing models. | Clean migration. |
| New `ResolutionCandidate` model | **Low**. New table with FKs to Node and Ingestion. | Clean migration. |
| PostgreSQL migration | **Medium**. Must export/import existing SQLite data. | Do early before more data accumulates. |

### Architectural Conflicts

| Issue | Severity | Recommendation |
|---|---|---|
| `parser.py` bypasses DSL — `sync_connections()` does direct ORM writes | **Medium**. Must be refactored before pipeline goes live or you'll have two code paths for graph mutation. | Phase 1 Task 1.2 handles this. Do it early. |
| `api_import_nodes` bypasses pipeline — Markdown import currently does its own entity creation inline | **Medium**. Once pipeline exists, imported markdown should route through extraction for AI processing. | Add a flag: `use_pipeline=True` routes through Celery tasks, `use_pipeline=False` keeps current fast-path for bulk imports. |
| SQLite as default database | **High**. JSONField queries for email matching (`properties__Email=email`) are fragile on SQLite. Celery + SQLite = lock errors. | Phase 0.0: migrate to PostgreSQL first. `psycopg` dependency already in `pyproject.toml`. |
| No authentication | **High**. Pipeline creates data, has admin-level operations. No auth means anyone can trigger ingestions. | Out of MVP scope, but must be added before any deployment. |
| CSRF exempt on all write endpoints | **Medium**. Fine for local dev, dangerous in production. | Address with auth layer. Not blocking for pipeline work. |
| `Any` type in `models.py:48` | **Low**. `save(*args: Any, **kwargs: Any)` violates the "no Any types" rule. | Fix when touching the file. |

### Hard-to-Reverse Decisions

| Decision | Why It Matters |
|---|---|
| DSL as serializable command log | The DSL logs all commands to `Ingestion.dsl_commands` for replay/audit. Slightly more complex than plain function wrappers, but enables undo and debugging. Right call. |
| LLM provider choice | Switching between Anthropic and OpenAI later is doable (same interface pattern) but prompt engineering differs. Pick one now, make it swappable via interface. |
| Summary as structured JSON field | `Node.summary` is a dedicated JSONField, separate from user-written `notes`. Cleaner for programmatic updates but means frontend must display both. |
| Resolution cascade: name match never auto-links (people) | Prevents wrong-person merges but means more items in the resolution queue. Correct tradeoff for a CRM — wrong merges destroy trust. |
| COMPANY as first-class node (not just a string property) | More complex graph but enables per-company summaries, WORKS_AT edges, and "show me everyone at Acme" queries. Worth the cost. |
| Three separate ingestion paths | More code than a single endpoint, but each source type has genuinely different I/O (audio file vs. document file vs. JSON text). Separate services are independently testable and replaceable. |

---

## 6. Pipeline Architecture

```
                    ┌─────────────────┐
                    │   VOICE NOTE    │
                    │ POST /ingest/   │
                    │     voice/      │
                    └────────┬────────┘
                             │ process_voice_note
                             │ (transcribe audio)
                             │
                    ┌────────┴────────┐
                    │   DOCUMENT      │
                    │ POST /ingest/   │──────────┐
                    │    document/    │           │
                    └────────┬────────┘           │
                             │ process_document   │
                             │ (extract text)     │
                             │                    │
                    ┌────────┴────────┐           │
                    │  FREEFORM NOTE  │           │
                    │ POST /ingest/   │           │
                    │      note/      │           │
                    └────────┬────────┘           │
                             │ process_freeform   │
                             │ (passthrough)      │
                             │                    │
                    ┌────────▼────────────────────▼─┐
                    │     SHARED PIPELINE            │
                    │                                │
                    │  extract_entities (LLM)        │
                    │         │                      │
                    │  resolve_entities               │
                    │         │                      │
                    │  write_graph (DSL)              │
                    │         │                      │
                    │  summarize (LLM)               │
                    │    ├─ per-meeting               │
                    │    ├─ per-person                │
                    │    └─ per-company               │
                    └────────────────────────────────┘
```

---

## 7. Priority Order

If building solo and need to ship something testable fast:

| Order | Phase | What |
|---|---|---|
| 1 | 0.0 | **PostgreSQL** — Celery + SQLite = database-locked errors |
| 2 | 0.1–0.6 + 0.8 | **Foundation** — Models, Celery, schema. Nothing runs without this |
| 3 | 1 | **DSL layer** — Everything writes through this. Get it right early |
| 4 | 2 | **Ingestion** — Three separate paths. Build freeform first (simplest), then document, then voice |
| 5 | 3 | **Extraction** — Now input produces structured data |
| 6 | 4 | **Resolution** — Now extracted people + companies link to your graph |
| 7 | 5 | **Graph writing** — Now the graph actually updates |
| 8 | 6 | **Summarization** — Now the data is useful. Per-meeting first, per-person second, per-company third |
| 9 | 0.7 | **Seed import** — Do this whenever you want to test with real data |
| 10 | 7 | **Frontend** — Do this last because you can test everything via API first |

---

## Deferred (Post-MVP Backlog)

- TOPIC, COMMITMENT, ACTION_ITEM node types
- Commitment scheduling + ScheduledReminder model
- Resolution Queue polling/badges
- Pre-meeting briefs (requires calendar sync)
- Retrieval layer / search (add when there's enough data)
- Notification system
- Calendar and email sync

---

## Decisions Needed Before Implementation

1. **LLM provider**: Anthropic Claude or OpenAI? (Or both behind an interface?)
2. **Existing TTS API**: What's the endpoint/SDK? Interface details needed for wrapping.
3. **Summary field vs. notes section**: Separate `summary` JSONField on Node (recommended) or structured section within existing `notes`?
