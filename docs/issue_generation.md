# Diagnostic Issue Generation & Interactive CLI Selection

The `workflow-clinic create-issue` command reads diagnostic findings from `diagnosis.json`, groups them by category domain (`containerization`, `resources`, `portability`, `security`), and provides an interactive selection menu before publishing to GitHub or exporting locally.

---

## Key Features

- **Category Grouping**: Combines multiple findings belonging to the same cloud readiness category into a single actionable GitHub issue.
- **Finding-Level SHA-256 Deduplication**: Embeds hidden HTML comments (`<!-- workflow-clinic:fingerprint:HASH -->`) into published issue bodies. Future runs extract these comments to filter out already-reported findings.
- **Interactive Selection Table**: Rich terminal UI (`[1]`, `[2]`, `[3]`) displaying issue category titles, severity color tags, and location counts.
- **Flexible Input Parsing**: Supports ranges (`1-3`), comma-separated lists (`1, 3`), `all`, `a`, and default empty inputs.
- **Non-TTY Auto-Detection**: Automatically detects piped or non-interactive terminal environments (`not sys.stdin.isatty()`) and selects all findings with a warning.
- **Dry-Run & Terminal Preview**: `--dry-run` prints Markdown payload directly to stdout without writing files; `--preview` renders terminal Markdown previews using Rich.
- **Local Fallback Mode ("Off Switch")**: Saves issues locally to `issue.md` without requiring GitHub credentials, providing an offline fallback when GitHub access is disabled or unavailable.

---

## Command Usage

```bash
# Basic usage — inspect diagnosis.json and launch interactive selection UI
workflow-clinic create-issue .

# Non-interactive CI mode — select all findings automatically
workflow-clinic create-issue . --all

# Dry-run — print generated Markdown in terminal without writing to disk
workflow-clinic create-issue . --dry-run

# Terminal preview — render Markdown preview before exporting
workflow-clinic create-issue . --preview

# Specify custom output path
workflow-clinic create-issue . --output custom_issue.md
```

---

## Authentication & Configuration

When publishing directly to GitHub repositories (via PyGitHub integration):

| Parameter | Environment Variable | CLI Option | Description |
|---|---|---|---|
| **GitHub Access Token** | `GITHUB_TOKEN` | `--token <PAT>` | Personal Access Token with `public_repo` / `repo` scope. |
| **Target Repository** | `GITHUB_REPOSITORY` | `--repo owner/repo` | Destination repository (`owner/repo`). |
| **Local Export Fallback** | N/A | `--local` | Forces local file export to `issue.md` (off switch for GitHub API). |

---

## Deduplication & Selection Architecture

```
[ Finding 1 (Tracked) ]  \
[ Finding 2 (Untracked) ]  -->  filter_new_findings()  -->  [ Finding 2 ]  -->  group_findings()  -->  Interactive CLI Menu  -->  Selected Issues
[ Existing Fingerprints ] /
```

1. **Extraction**: `extract_fingerprints()` parses active repository issues for `<!-- workflow-clinic:fingerprint:<hash> -->` tags.
2. **Filtering**: `filter_new_findings()` removes findings whose structural SHA-256 hashes already exist in the repository.
3. **Grouping**: `group_findings()` aggregates remaining new findings by category domain (`containerization`, `resources`, `portability`, `security`).
4. **Interactive Selection**: User selects issue indices interactively, or `--all` selects all issue groups.
