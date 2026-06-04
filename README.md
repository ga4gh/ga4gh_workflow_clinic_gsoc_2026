# Workflow Clinic

Workflow Clinic is a GSoC 2026 project focused on improving the portability, reproducibility, and cloud-readiness of scientific workflows.

The project aims to analyze workflow languages such as Nextflow and Snakemake, convert them into a common intermediate representation called **WorkflowBundle**, and identify workflow portability issues through automated validation and analysis.

By using a common workflow model inspired by the DAW (Data Analysis Workflow) metamodel, Workflow Clinic can reason about workflows independently of their original language and provide consistent diagnostics, recommendations, and future repair capabilities.

## Why Workflow Clinic?

Scientific workflows are often tightly coupled to specific execution environments, storage systems, schedulers, or local infrastructure.

This can make workflows difficult to:

- Share
- Reproduce
- Port across platforms
- Execute in cloud environments
- Integrate with GA4GH-compliant services

Workflow Clinic aims to help workflow authors identify and resolve these issues before deployment.

## Planned Features

### Workflow Parsing

- Nextflow support
- Snakemake support
- Common WorkflowBundle representation

### Workflow Analysis

- Portability diagnostics
- Storage validation
- Resource validation
- Metadata validation
- Workflow structure validation

### AI-Assisted Review

- Rule-based workflow checks
- AI-assisted diagnostics
- Confidence-based recommendations

### Workflow Repair

- Suggested fixes
- Automated transformations
- Validation of generated fixes

## Installation

### Clone the Repository

```bash
git clone https://github.com/revaarathore11/ga4gh_workflow_clinic_gsoc_2026-.git
cd ga4gh_workflow_clinic_gsoc_2026-
```

### Create a Virtual Environment

```bash
python -m venv .venv
source .venv/bin/activate
```

### Install Dependencies

```bash
pip install -e ".[dev]"
```

## Development

### Run Tests

```bash
pytest
```

### Run Linting

```bash
ruff check .
```

### Run Formatting

```bash
ruff format .
```

## Project Structure

```
├── .github/workflows/     # GitHub Actions CI
├── docs/                  # Research and architecture documents
├── fixtures/              # Benchmark workflows
├── src/workflow_clinic/   # Main source code
├── tests/                 # Automated tests
├── pyproject.toml
└── README.md
```

## Documentation

Project documentation is organized into:

```
docs/research/             # Paper notes and literature review
docs/architecture/         # Design documents and specifications
docs/workflow-analysis/    # Manual workflow analysis reports
```

## Supported Workflow Languages

Current target languages:

- Nextflow
- Snakemake

Potential future support:

- CWL
- WDL

## Architecture Overview

```
Workflow Files
    ↓
  Parser
    ↓
WorkflowBundle
    ↓
Rule Engine
    ↓
 AI Critic
    ↓
  Doctor
```

## Standards Alignment

Workflow Clinic is being designed with future compatibility in mind for:

- GA4GH TES
- GA4GH WES
- GA4GH TRS
- Workflow Run RO-Crate

## License

Apache License 2.0
