# Workflow Clinic – Architecture

## Project Goal

Workflow Clinic is a tool that analyzes scientific workflows and identifies portability,
reproducibility, and cloud-readiness issues.

The initial workflow languages supported will be Nextflow and Snakemake, with potential
future support for CWL and WDL.

The objective is to help workflow authors prepare workflows for execution on cloud and
GA4GH-compliant infrastructure.

---

## Core Design Principle

The project should not analyze raw workflow files directly throughout the entire pipeline.

Instead, every workflow should first be converted into a common intermediate representation
called **WorkflowBundle**.

```
Nextflow  → WorkflowBundle
Snakemake → WorkflowBundle
```

Once converted, all downstream components operate on WorkflowBundle instead of
language-specific syntax.

This idea is inspired by the DAW Metamodel paper (Hilbrich et al., 2022; [PDF](https://api-depositonce.tu-berlin.de/server/api/core/bitstreams/3712c6d8-2bef-4528-ad75-1c4b3e667c3e/content)), which proposes a common representation for different workflow languages.

> All workflow languages are converted into a common WorkflowBundle representation before
> analysis. All downstream components operate on WorkflowBundle rather than language-specific
> syntax.

---

## High-Level Architecture

```
Workflow Files
      ↓
   Parsers
      ↓
WorkflowBundle
      ↓
 Rule Engine
      ↓
   Findings
      ├─ AI Critic (Optional)
      └─ Doctor (Future)
```

---

## Parsers

The purpose of the parsers is to convert workflow files into WorkflowBundle objects.

**Initial parsers:**
- Nextflow Parser
- Snakemake Parser

The parser layer is the **only** part of the system that should interact directly with
workflow source files. Everything after parsing should operate only on WorkflowBundle.

---

## WorkflowBundle

WorkflowBundle is the most important structure in the entire project.

WorkflowBundle is built around the DAW concepts of **Tasks**, **Interconnections**, and
**Storage**, and extended with **Metadata** and **Execution Context** for workflow
portability analysis.

- **Tasks** — units of work (processes, rules)
- **Interconnections** — dependencies and data flow between tasks
- **Storage** — persistent, intermediate, and interactive data
- **Metadata** — workflow-level information (name, author, version)
- **Execution Context** — runtime details (containers, resources, executors)

This design is directly inspired by the DAW (Data Analysis Workflow) Metamodel paper,
which proposes a common representation for modeling workflows across different languages.

---

### Tasks

A Task represents a unit of work performed by a workflow.

**Examples:**
- FASTQC
- ALIGN
- QUANTIFY
- VARIANT_CALLING

A Nextflow **process** and a Snakemake **rule** should both become Tasks inside
WorkflowBundle. Workflow Clinic should treat them identically after parsing.

---

### Interconnections

Interconnections represent how tasks communicate and depend on each other.

**Example:**
```
FASTQC → ALIGN → QUANTIFY
```

- In Nextflow, interconnections correspond to **Channels**
- In Snakemake, initially, interconnections are inferred through **rule input/output
  relationships**. This mapping may evolve as the project and metamodel mature.

> The DAW paper emphasizes that workflows are not only collections of tasks.
> The relationships between tasks are equally important and should be modeled explicitly.

---

### Storage

Storage represents data used or produced by workflows.

#### Persistent Storage
Long-lived data.

Examples:
- Input FASTQ files
- BAM files
- Result directories
- Databases

#### Intermediate Storage
Temporary data produced between tasks.

Examples:
- Channel values
- Temporary files
- Pipes

#### Interactive Storage
Runtime values provided by users.

Examples:
- Parameters
- Configuration values
- Command-line inputs

---

### Execution Context

Execution Context describes how tasks are executed.

Examples:
- Containers
- CPU requirements
- Memory requirements
- Error handling strategies
- Executor configuration

> For Workflow Clinic, many portability problems will be detected inside this section.

---

### Metadata

Metadata contains workflow-level information.

Examples:
- Name
- Author
- Version
- Description
- License

This information is important for WorkflowHub, Dockstore, and GA4GH ecosystems.

---

## Rule Engine

The Rule Engine is the first analysis layer.

- It **always runs before any AI component**
- No API key required
- Identifies deterministic issues only

### Containerization Rules
- Missing container
- Unpinned container tag
- Local `.sif` reference
- Conda-only environment

### Storage Rules
- Absolute paths
- Hardcoded paths
- Local filesystem assumptions

### Resource Rules
- Hardcoded CPU counts
- Hardcoded memory values
- Slurm-specific directives
- Cluster-specific assumptions

### Metadata Rules
- Missing version
- Missing author
- Missing description

### Workflow Structure Rules
- Missing inputs
- Missing outputs
- Disconnected tasks
- Broken task relationships

---

## AI Critic (Optional)

**Workflow Clinic must function completely without AI.** The Rule Engine is the primary
analysis mechanism and provides all core diagnostics.

The AI Critic is an **optional enhancement layer** that runs only when deterministic rules
cannot confidently decide. It requires an API key and is not needed for normal operation.

The project should remain **rule-first and AI-optional**.

### Confidence Tiers

| Tier | Source | Confidence | Example |
|---|---|---|---|
| Tier 0 | Rule Engine | 1.0 | Missing container field |
| Tier 1 | AI Verification | ≥ 0.75 | Is Conda-only intentional? |
| Tier 2 | AI Deep Analysis (RAG) | 0.6–0.85 | Undocumented dependencies |

---

## Doctor (Future)

The Doctor component proposes fixes.

> **Note:** Automatic workflow repair is outside the scope of the initial MVP. This
> capability will be explored after the parser and rule engine are stable and validated.

**Planned three-layer strategy:**

| Layer | Method | Example |
|---|---|---|
| Layer 1 | AST-based (safest) | Insert missing container directive |
| Layer 2 | Regex / template | Replace `:latest` tags with pinned versions |
| Layer 3 | AI-generated (last resort) | Complex restructuring, grounded via RAG |

---

## RAG Knowledge Base

**Purpose:** Reduce hallucinations and ground AI responses.

**Sources:**
- nf-core guidelines
- GA4GH specifications
- BioContainers documentation
- WorkflowHub documentation
- Dockstore documentation
- Nextflow documentation
- Snakemake documentation

**Storage:** ChromaDB (local, offline)

---

## Verification Layer

Any proposed fix must be validated before publication.

- **Nextflow:** Run Nextflow validation commands
- **Snakemake:** Run Snakemake linting

If validation fails, the fix is rejected.

---

## GA4GH Alignment

WorkflowBundle should eventually align with:

- WES (Workflow Execution Service)
- TES (Task Execution Service)
- TRS (Tool Registry Service)
- Workflow Run RO-Crate

> The goal is not to directly copy these standards but to ensure WorkflowBundle can
> map to them in the future.

---

## Key Lessons from the DAW Metamodel Paper

1. Different workflow languages should be converted into a common model
2. Workflows are not just tasks — the **relationships between tasks** must also be modeled
3. Storage should be treated as a **first-class concept**
4. Execution details (containers, resources) should be represented **separately from workflow logic**
5. A common metamodel makes portability analysis, transformation, validation, and future workflow translation possible

> This is the main architectural foundation for Workflow Clinic.
