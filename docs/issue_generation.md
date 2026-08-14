# Diagnostic Issue Generation & Interactive CLI Selection

The `workflow-clinic create-issue` command reads diagnostic findings from `diagnosis.json`, performs structural SHA-256 deduplication against existing GitHub issues, groups new findings by category domain (`containerization`, `resources`, `portability`, `security`), and provides an interactive selection menu before publishing directly to GitHub or exporting to a local Markdown file.

---

## Key Features

- **Category Grouping**: Combines multiple findings belonging to the same cloud readiness category into a single actionable GitHub issue.
- **Finding-Level SHA-256 Deduplication**: Embeds hidden HTML comments (`<!-- workflow-clinic:fingerprint:HASH -->`) into published issue bodies. Online runs extract these comments to filter out already-reported findings.
- **Direct GitHub Publishing**: Creates live GitHub issues in target repositories with automatic `workflow-clinic` label assignment (`#d73a4a`).
- **Interactive Selection Table**: Rich terminal UI (`[1]`, `[2]`, `[3]`) displaying issue category titles, severity color tags, and location counts.
- **Flexible Input Parsing**: Supports ranges (`1-3`), comma-separated lists (`1, 3`), `all`, `a`, and default empty inputs.
- **Non-TTY Auto-Detection**: Automatically detects piped or non-interactive terminal environments (`not sys.stdin.isatty()`) and selects all findings with a warning.
- **Dry-Run & Terminal Preview**: `--dry-run` prints Markdown payload directly to stdout without writing files or calling APIs; `--preview` renders terminal Markdown previews using Rich.
- **Local Fallback Mode ("Off Switch")**: `--local` forces local file export to `issue.md` without requiring GitHub credentials, providing an offline fallback when GitHub access is disabled or unavailable.

---

## Command Usage

```bash
# Direct online GitHub issue publishing using PAT and target repo
workflow-clinic create-issue . --token ghp_12345 --repo owner/repo

# Seamless CI/CD integration using environment variables
export GITHUB_TOKEN="ghp_12345"
export GITHUB_REPOSITORY="owner/repo"
workflow-clinic create-issue . --all

# Force local offline Markdown export
workflow-clinic create-issue . --local

# Basic usage — inspect diagnosis.json and launch interactive selection UI
workflow-clinic create-issue .

# Dry-run — print generated Markdown in terminal without writing to disk or calling GitHub APIs
workflow-clinic create-issue . --dry-run

# Terminal preview — render Markdown preview before exporting
workflow-clinic create-issue . --preview

# Specify custom output path for local export
workflow-clinic create-issue . --output custom_issue.md
```

---

## Authentication & Configuration

When publishing directly to GitHub repositories (via PyGitHub integration):

| Parameter | Environment Variable | CLI Option | Description |
|---|---|---|---|
| **GitHub Access Token** | `GITHUB_TOKEN` | `--token <PAT>` | Personal Access Token with `public_repo` (public repos) or `repo` (private repos) scope. |
| **Target Repository** | `GITHUB_REPOSITORY` | `--repo owner/repo` | Destination repository (`owner/repo`). |
| **Local Export Fallback** | N/A | `--local` | Forces local file export to `issue.md` (off switch for GitHub API). |

---

## Deduplication & Selection Architecture

```
[ Finding 1 (Tracked) ]  \
[ Finding 2 (Untracked) ]  -->  filter_new_findings()  -->  [ Finding 2 ]  -->  generate_issues()  -->  Interactive CLI Menu  -->  PyGitHub Publisher / Local Export
[ Existing Fingerprints ] /
```

1. **Extraction**: `fetch_active_fingerprints()` queries active repository issues for `<!-- workflow-clinic:fingerprint:<hash> -->` comments via PyGitHub.
2. **Filtering**: `filter_new_findings()` removes findings whose structural SHA-256 hashes already exist in open issues.
3. **Grouping**: `group_findings()` aggregates remaining new findings by category domain (`containerization`, `resources`, `portability`, `security`).
4. **Interactive Selection**: User selects issue indices interactively, or `--all` selects all issue groups.
5. **Publishing / Export**: `GitHubPublisher.publish_issue()` posts live issues online with auto-assigned labels, or saves to local `issue.md`.

