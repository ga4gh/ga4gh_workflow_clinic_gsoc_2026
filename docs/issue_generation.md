# Diagnostic Issue Generation & GitHub Integration

The `workflow-clinic create-issue` command formats diagnostic findings from `diagnosis.json` into GitHub issue payloads, grouped by category domain (`containerization`, `resources`, `portability`, `security`).

---

## Key Features

- **Category Grouping**: Combines multiple findings belonging to the same cloud readiness category into a single actionable GitHub issue.
- **Finding-Level SHA-256 Deduplication**: Embeds hidden HTML comments (`<!-- workflow-clinic:fingerprint:HASH -->`) into published issue bodies. Future runs extract these comments to filter out already-reported findings before grouping.
- **Interactive Selection Table**: Interactive CLI table allowing users to preview and select specific issue groups to export or publish.
- **Local Fallback ("Off Switch")**: Saves issues locally to `issue.md` without requiring GitHub credentials, providing an offline fallback when GitHub access is disabled or unavailable.

---

## Command Usage

```bash
# Basic usage — inspect diagnosis.json and launch interactive selection
workflow-clinic create-issue .

# Non-interactive CI mode — select all findings automatically
workflow-clinic create-issue . --all

# Dry-run — preview generated Markdown in terminal without writing to disk
workflow-clinic create-issue . --dry-run

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

## Deduplication Architecture

```
[ Finding 1 (Tracked) ]  \
[ Finding 2 (Untracked) ]  -->  filter_new_findings()  -->  [ Finding 2 ]  -->  group_findings()  -->  Generated Issue
[ Existing Fingerprints ] /
```

1. **Extraction**: `extract_fingerprints()` parses active repository issues for `<!-- workflow-clinic:fingerprint:<hash> -->` tags.
2. **Filtering**: `filter_new_findings()` removes findings whose structural SHA-256 hashes already exist in the repository.
3. **Grouping**: `group_findings()` aggregates remaining new findings by category domain (`containerization`, `resources`, `portability`, `security`).
