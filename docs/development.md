# Development Guide: Creating New Workflow Parsers

This guide is for contributors who want to understand how parsing works in
Workflow Clinic, use the parser layer programmatically, or add support for a
new workflow language (e.g. Snakemake, CWL, WDL).

## Table of Contents

1. [Overview of Parsing Architecture](#1-overview-of-parsing-architecture)
2. [Using Parsers in Python (Library Imports)](#2-using-parsers-in-python-library-imports)
3. [How to Write a New Parser Class](#3-how-to-write-a-new-parser-class)
4. [Common Pitfalls](#4-common-pitfalls)
5. [How to Write a New Rule](#5-how-to-write-a-new-rule)
6. [Related Files](#6-related-files)

---

## 1. Overview of Parsing Architecture

All workflow files (e.g. Nextflow, Snakemake) must be parsed into a common
intermediate representation called the **`WorkflowBundle`**. The parser layer
is the only part of the codebase that interacts directly with
language-specific syntax — everything downstream (rule engine, AI Critic
agents, CLI) only ever sees a `WorkflowBundle`.

```
Workflow Files (Nextflow/Snakemake)
      ↓
   Parsers (BaseParser implementations)
      ↓
WorkflowBundle (Tasks + Resources + Metadata)
      ↓
 Rule Engine (diagnostics check)
```

### The `BaseParser` contract

Every parser must implement the `BaseParser` abstract class defined in `src/workflow_clinic/parsers/base.py`.

It requires implementing:
- `can_parse(cls, path: Path) -> bool` — returns `True` if the parser is compatible with the path.
- `parse(self, path: Path, entrypoint: str | None = None) -> WorkflowBundle` — parses the path and returns a `WorkflowBundle`, wrapping any syntax/structural parser exceptions in `InvalidWorkflowError`.

---

## 2. Using Parsers in Python (Library Imports)

The `ParserRegistry` automatically routes a workflow file or directory to the
correct parser based on `can_parse()`.

```python
from pathlib import Path
from workflow_clinic.parsers import ParserRegistry
from workflow_clinic.exceptions import InvalidWorkflowError

workflow_path = Path("tests/fixtures/dummy.nf")

# 1. Dynamically detect which parser is compatible with the path
parser_name = ParserRegistry.detect_parser(workflow_path)
print(f"Detected parser: {parser_name}")

# 2. Retrieve the registered parser instance
parser = ParserRegistry.get_parser(parser_name)

# 3. Parse the file into a common WorkflowBundle
try:
    bundle = parser.parse(workflow_path)
except InvalidWorkflowError as e:
    print(f"Failed to parse workflow: {e}")
    raise

# 4. Walk the extracted tasks and resources
print(f"Workflow name: {bundle.metadata.name}")
for task in bundle.tasks:
    print(f"Task: {task.name}")
    print(f"  Container: {task.resources.container}")
    print(f"  CPUs: {task.resources.cpus}")
    print(f"  Memory: {task.resources.memory}")
```

**Actual output** (verified against `tests/fixtures/dummy.nf`):

```text
Detected parser: nextflow
Workflow name: dummy
Task: FASTQC
  Container: quay.io/biocontainers/fastqc:0.12.1--hdfd78af_0
  CPUs: 2
  Memory: 4
Task: TRIM_READS
  Container: quay.io/biocontainers/trim-galore:0.6.10--hdfd78af_0
  CPUs: 4
  Memory: 8 GB
Task: ALIGN
  Container: quay.io/biocontainers/bwa:0.7.17--hed695b0_7
  CPUs: 8
  Memory: 16 GB
```

> **Known limitation — closure-based directives:** FASTQC declares
> `memory { 4.GB * task.attempt }`, a Groovy closure. The AST walker does
> **not** evaluate closures; it performs a depth-first search and returns
> the first numeric literal it finds (`4`), ignoring unit suffixes (`.GB`)
> and arithmetic (`* task.attempt`). This means `memory { 4.GB * task.attempt }`
> and `memory { 4.GB * task.attempt * 2 }` both produce `Memory: 4`.
> Plain-string directives like `memory "8 GB"` are extracted verbatim and retain their units.

### Why `dummy.nf` looks the way it does

`tests/fixtures/dummy.nf` is modeled on real **nf-core** DSL2 pipelines rather than a hand-simplified example, so it exercises the same edge cases a real workflow would:

- **Diverse directive formats** — `cpus`, plain-string memory (`"8 GB"`), and closure-based `memory { 4.GB * task.attempt }`.
- **Named outputs** — `emit:` syntax, common in nf-core modules and a frequent source of AST-walker bugs.
- **Realistic interpolation** — `"${params.outdir}/trimmed"`, `baseDir` references, instead of hardcoded literals.
- **Compliance baseline** — every process has a `container`, `tag`, `cpus`, and `memory`, so this fixture should produce zero findings when run through the Rule Engine. (A second fixture `tests/fixtures/poor_practices.nf` contains intentional gaps to verify rule violations.)

---

## 3. How to Write a New Parser Class

To add support for a new workflow language (e.g. `MyLanguage`):

### Step 1: Create the Parser Module

Create a new file under `src/workflow_clinic/parsers/`, e.g. `src/workflow_clinic/parsers/my_language.py`, inheriting from `BaseParser`.

#### Implement `can_parse`
Define `can_parse` to detect files using extensions or names:

```python
from pathlib import Path
from workflow_clinic.parsers.base import BaseParser

class MyLanguageParser(BaseParser):
    """Parser implementation for MyLanguage workflows."""

    @classmethod
    def can_parse(cls, path: Path) -> bool:
        if path.is_file():
            return path.suffix == ".mylang"
        return False
```

#### Implement `parse`
Define `parse` to extract `WorkflowMetadata` and `Task` resources, wrapping any parser errors inside `InvalidWorkflowError`:

```python
from pathlib import Path
from workflow_clinic.exceptions import InvalidWorkflowError
from workflow_clinic.models import WorkflowBundle, WorkflowMetadata

    def parse(self, path: Path, entrypoint: str | None = None) -> WorkflowBundle:
        try:
            # Custom parsing/AST traversal logic here...
            metadata = WorkflowMetadata(name=path.stem)
            tasks = []  # Populate with Task objects
        except Exception as exc:
            raise InvalidWorkflowError(f"Failed to parse {path}: {exc}") from exc

        return WorkflowBundle(metadata=metadata, tasks=tasks)
```

`InvalidWorkflowError` must always wrap the original exception (`from exc`) so the traceback is preserved for debugging.

### Step 2: Avoid Regex — Use AST or Parsing Libraries

Do not write custom regular expressions or manual brace-counters. They are
fragile against string interpolation, nested quotes, and comments. Use a
structured library instead:

- **Nextflow/Groovy** → `groovy-parser` (Lark-based)
- **Snakemake/Python** → Python's native `ast` module, or the official
  Snakemake API
- **Other languages** → a verified Lark grammar or Tree-sitter binding

### Step 3: Register the Parser

Add the import and registration call in `src/workflow_clinic/parsers/__init__.py`:

```python
from workflow_clinic.parsers.registry import ParserRegistry
from workflow_clinic.parsers.nextflow import NextflowParser
from workflow_clinic.parsers.my_language import MyLanguageParser

ParserRegistry.register("nextflow", NextflowParser)
ParserRegistry.register("mylanguage", MyLanguageParser)
```

### Step 4: Map Objects to the Common Schema

Map your workflow's processes/rules onto the shared models:

- **`Task`** — a process block or execution step.
- **`TaskResources`** — resource requests: `cpus` (int), `memory` (str),
  `container` (str).

### Step 5: Write Unit and Integration Tests

Add tests under `tests/` (e.g. `tests/test_my_language_parser.py`) covering:

- **Detection** — `can_parse()` is correct for valid files, unrelated files, and directories.
- **Translation** — task names and directives map to the expected resource values.
- **Syntax errors** — invalid input raises `InvalidWorkflowError`, not a raw parser exception.

Run the suite with:

```bash
pytest tests/test_my_language_parser.py -v
```

---

## 4. Common Pitfalls

- **Forgetting to register the parser** in
  `src/workflow_clinic/parsers/__init__.py` — `can_parse()` working in
  isolation doesn't mean the registry will find it.
- **Circular imports** — don't import `ParserRegistry` from inside your
  parser module; register from `__init__.py` instead.
- **Swallowing the original exception** — always use
  `raise InvalidWorkflowError(...) from exc`, never a bare
  `raise InvalidWorkflowError(...)`, or you lose the traceback.
- **Only testing the happy path** — every new parser needs at least one
  test that feeds it deliberately broken input and asserts
  `InvalidWorkflowError` is raised.

## 5. How to Write a New Rule

To add a new validation check or portability rule:

### Step 1: Create the Rule Module

Create a new file under `src/workflow_clinic/rules/`, e.g. `src/workflow_clinic/rules/custom.py`, inheriting from the `BaseRule` class defined in `src/workflow_clinic/rules/base.py`.

#### Define Rule Attributes
Every rule must define `id`, `name`, and `description` attributes:

```python
from workflow_clinic.rules.base import BaseRule

class MyCustomRule(BaseRule):
    """Flag workflows violating custom conditions (W003)."""

    id = "W003"
    name = "My Custom Rule"
    description = "Checks for custom workflow validation patterns."
```

#### Implement the `check` method
Implement `check(self, bundle: WorkflowBundle) -> list[Finding]`. Return a list of `Finding` objects (with correct `Severity` level) if violations are detected:

```python
from workflow_clinic.models import WorkflowBundle
from workflow_clinic.rules.base import BaseRule, Finding, Severity

    def check(self, bundle: WorkflowBundle) -> list[Finding]:
        findings = []
        # Check specific validation conditions
        if not bundle.metadata.name:
            findings.append(
                Finding(
                    rule_id=self.id,
                    message="Workflow metadata is missing a name.",
                    severity=Severity.WARNING,
                )
            )
        return findings
```

### Step 2: Register the Rule

Import and register the rule in `src/workflow_clinic/rules/__init__.py`:

```python
from workflow_clinic.rules.custom import MyCustomRule

# Register your custom rule
RuleRegistry.register(MyCustomRule)
```

### Step 3: Write Automated Tests

Add tests to `tests/test_rules.py` covering your rule class:
- Instantiate your rule and verify `check()` returns findings on flawed inputs and an empty list on compliant inputs.
- Run the tests with `pytest tests/test_rules.py -v`.

---

## 6. Related Files

| File | Purpose |
|------|---------|
| `tests/fixtures/dummy.nf` | Realistic DSL2 test fixture |
| `tests/fixtures/poor_practices.nf` | Flawed DSL2 test fixture demonstrating violations |
| `src/workflow_clinic/parsers/nextflow.py` | Nextflow parser implementation |
| `src/workflow_clinic/rules/base.py` | `BaseRule` interface, `Finding` model, and `Severity` enum |
| `src/workflow_clinic/rules/registry.py` | `RuleRegistry` lookup mechanics |
| `src/workflow_clinic/rules/runner.py` | `RuleRunner` logic |
| `src/workflow_clinic/rules/container.py` | `PinnedContainerRule` implementation |
| `src/workflow_clinic/rules/resources.py` | `ResourceLimitsRule` implementation |
| `src/workflow_clinic/exceptions.py` | Exception hierarchy |
| `tests/test_rules.py` | Rule engine validation tests |
| `tests/test_cli.py` | Command-line interface and diagnostics checks |

