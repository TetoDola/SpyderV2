# Django + HTMX Project

> Claude Code configuration for a modern Django project utilizing HTMX, dynamic data schemas, and interactive frontend visualizations.

## Quick Facts

- **Stack**: Django, PostgreSQL, HTMX, Vanilla JS (for Canvas/Graph rendering)
- **Package Manager**: uv
- **Test Command**: `uv run pytest`
- **Lint Command**: `uv run ruff check .`
- **Format Command**: `uv run ruff format .`
- **Type Check**: `uv run pyright`

## Key Directories

- `apps/` - Django applications
- `config/` - Django settings and root URLconf
- `templates/` - Django/Jinja2 templates (including HTMX partials)
- `static/` - CSS, JavaScript (including canvas logic), images
- `tests/` - Test files
- `tasks/` - Celery tasks

## Code Style

- Python 3.12+ with type hints required
- Ruff for linting and formatting (replaces black, isort, flake8)
- pyright strict mode enabled
- No `Any` types - use proper type hints or `object`
- Use early returns, avoid nested conditionals
- Prefer composition over inheritance

## Git Conventions

- **Branch naming**: `{initials}/{description}` (e.g., `jd/add-parser-utility`)
- **Commit format**: Conventional Commits (`feat:`, `fix:`, `docs:`, etc.)
- **PR titles**: Same as commit format

## Project-Specific Architectural Rules

### Data Modeling & Dynamic Schemas
- **Dynamic Properties**: Prefer using `JSONField` for user-defined or highly variable attributes rather than hardcoding strict database columns. 
- **Entity Relationships**: When linking distinct entities together, utilize explicit database relationships (ForeignKeys/Edges) rather than storing relationships as raw text, ensuring the data can be easily queried and mapped.

### Backend Text Parsing
- The backend utilizes utility functions to parse specific text patterns (e.g., `[[Entity]]` tags) within text and JSON fields. 
- This parsing logic must intercept save/update operations to automatically resolve entities and generate database relationship records behind the scenes. Keep this logic modular and heavily tested.

### Frontend Integration (HTMX + JS Canvas)
- **Hybrid Approach**: Use HTMX for standard UI interactions, form submissions, sidebars, and modals. Use Vanilla JS fetching lightweight JSON APIs for rendering the interactive visualization canvas.
- Ensure the JavaScript canvas state updates gracefully without requiring full page reloads when HTMX partials are submitted.

## Standard Critical Rules

### Error Handling
- NEVER swallow errors silently.
- Always show user feedback for errors (Django messages, HTMX response headers).
- Log errors with proper context for debugging, especially within the text parsing engine.

### Views & APIs
- Prefer Function-Based Views.
- Always validate request.method explicitly.
- Return proper HTTP status codes.
- Use `select_related()` / `prefetch_related()` to avoid N+1 queries, particularly when serializing nested relationships.

### Templates & HTMX
- Use template inheritance (`{% extends %}`, `{% block %}`).
- Create partial templates for HTMX responses (`_partial.html` naming).
- Always include `hx-indicator` for loading states.
- Handle `HX-Request` header for partial vs full page responses.

### Forms
- Use ModelForm for model-backed forms.
- Validate in `clean()` and `clean_<field>()` methods.
- Always handle form errors in templates.
- Disable submit buttons during HTMX requests.

### Testing

- Write failing test first (TDD).
- Use Factory Boy for mock data generation.
- Use pytest fixtures in `conftest.py`.
- Test behavior, not implementation (prioritize testing the text parsing and edge-creation logic).
- Run tests before committing.

## Skill Activation

Before implementing ANY task, check if relevant skills apply:

- Debugging issues → `systematic-debugging` skill
- Exploring Django project (models, URLs, settings) → `django-extensions` skill
- Creating new skills → `skill-creator` skill

## Common Commands

```bash
# Development
uv run python manage.py runserver     # Start dev server
uv run pytest                         # Run tests
uv run pytest -x --lf                 # Run last failed, stop on first failure
uv run ruff check .                   # Lint code
uv run ruff format .                  # Format code
uv run pyright                        # Type check

# Django
uv run python manage.py makemigrations
uv run python manage.py migrate
uv run python manage.py shell_plus    # Enhanced shell (django-extensions)

# Dependencies
uv sync                               # Install from pyproject.toml
uv add <package>                      # Add new dependency
uv add --dev <package>                # Add dev dependency