# Feature: Claude Code Tooling & Code Mode (Plan 58)

## Overview

Plan 58 introduces code-first navigation tools, a dedicated `/code` command, budget-based context compaction, and git undo/stash tools — inspired by the Claude Code developer experience.

## New Tools

### `glob_files` — File Discovery

Find files matching glob patterns across the project.

- **Backend**: `pathlib.Path.rglob`
- **Default excludes**: `.git`, `node_modules`, `__pycache__`, `.venv`, `.pytest_cache`, etc.
- **Cap**: 200 results max
- **Security**: Path traversal blocked via `is_relative_to(root)`

Usage: `glob_files(pattern="**/*.py", directory="app/", exclude="vendor")`

### `grep_code` — Regex Search

Search project files using ripgrep (`rg`) with fallback to `grep -rn`.

- **Context lines**: 0-10 surrounding lines per match
- **Include filter**: Comma-separated globs (e.g. `*.py,*.ts`)
- **Output cap**: 8000 chars, 50 matches max
- **Security**: Path traversal blocked

Usage: `grep_code(pattern="def validate_email", include="*.py", context_lines=2)`

### `git_undo` — Restore Files or Revert Commits

- `scope="file"`: `git checkout -- <file_path>` (restore to last committed state)
- `scope="commit"`: `git revert HEAD --no-edit` (create a revert commit)
- Flag injection blocked: paths starting with `-` are rejected

### `git_stash` — Stash Management

- `action="save"`: `git stash push [-m message]`
- `action="pop"`: `git stash pop`
- `action="list"`: `git stash list`

## `/code` Command

Start a coding agent session with pre-classified tool categories and a higher iteration limit (20 vs default 15).

```
/code fix the login validation bug
```

Activates: `code`, `selfcode`, `shell`, `workspace` categories — skipping intent classification for faster startup.

## Budget-Based Auto-Compaction

Before each LLM call in the tool loop, the system estimates context token usage. When it exceeds 80% of `CONTEXT_WINDOW_TOKENS` (default: 32768):

1. `microcompact_messages()` aggressively compacts old tool results
2. `_clear_old_tool_results()` replaces all but the last tool result with summaries

This prevents context overflow during long coding sessions.

## `read_source_file` Size Increase

Truncation limit increased from 5KB to 12KB, allowing full reading of most source files without needing `read_lines`.

## New `code` Category in Router

`TOOL_CATEGORIES["code"]` includes all coding tools:
- `glob_files`, `grep_code` (navigation)
- `read_source_file`, `read_lines`, `get_file_outline`, `list_source_files` (reading)
- `write_source_file`, `apply_patch`, `preview_patch` (writing)
- `run_command`, `manage_process` (shell)
- `git_status`, `git_diff`, `git_create_branch`, `git_commit`, `git_push`, `git_create_pr`, `git_undo`, `git_stash` (git)

`WORKER_TOOL_SETS["coder"]` updated to include `code` + `selfcode` + `shell` + `workspace`.

## Configuration

| Setting | Default | Description |
|---------|---------|-------------|
| `CONTEXT_WINDOW_TOKENS` | `32768` | Model context window for budget-based compaction |

## Files Changed

| File | Change |
|------|--------|
| `app/skills/tools/glob_tools.py` | **New** — `glob_files` tool |
| `app/skills/tools/grep_tools.py` | **New** — `grep_code` tool |
| `app/skills/tools/git_tools.py` | Added `git_undo`, `git_stash` |
| `app/skills/tools/selfcode_tools.py` | `read_source_file` limit 5KB→12KB |
| `app/skills/tools/__init__.py` | Register glob + grep tools |
| `app/skills/router.py` | Added `code` category, classifier examples, updated `WORKER_TOOL_SETS` |
| `app/skills/executor.py` | Budget-based compaction (`_budget_compact`) |
| `app/config.py` | Added `context_window_tokens` setting |
| `app/commands/builtins.py` | Added `/code` command |
| `app/agent/loop.py` | `pre_classified_categories` plumbing for `/code` |

## Tests

| Test File | Coverage |
|-----------|----------|
| `tests/test_glob_tools.py` | 6 tests: pattern matching, excludes, max results, directory scope, path traversal |
| `tests/test_grep_tools.py` | 6 tests: pattern search, context lines, include filter, no results, path traversal, truncation |
| `tests/test_git_undo.py` | 9 tests: undo file/commit, flag injection, stash save/pop/list, unknown scope/action |
| `tests/test_budget_compaction.py` | 3 tests: triggered, not triggered, boundary at 80% |
