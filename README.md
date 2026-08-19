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


### AI Critic & Remediation Guidance (AI-Assisted Review)

Workflow Clinic includes an **AI Critic Agent** to enrich diagnostic findings with AI-powered, cloud-readiness remediation advice.

### Enabling the AI Critic
To enable the AI to perform a high-level review of your workflow (discovering complex logic bugs, implicit dependencies, or anti-patterns) AND generate detailed remediation advice for all findings, run the `examine` command with the `--enhance` (`-e`) flag:
```bash
workflow-clinic examine main.nf --enhance
```

*Note: If no API key is provided, `--enhance` will fall back to using a local knowledge base for basic remediation and will skip the high-level audit discovery phase.*

#### Deduplication & Anti-Hallucination
The AI Critic is engineered with strict system prompts that feed it all previously discovered static issues (e.g., `W001`, `W002`). This prevents the LLM from duplicating existing findings, forcing it to focus exclusively on discovering complex bugs, shell scripting anti-patterns, and implicit dependencies that static rules miss. It is also strictly instructed not to hallucinate issues just to fill quotas.

### 🔑 Bring Your Own Key & Model (BYOK & BYOM)
You can configure any supported LiteLLM model (e.g., OpenAI, Gemini, Anthropic, Groq, Mistral) by setting the respective environment variable.

#### Configuration via `.env` file (Recommended)
Create a `.env` file in your working directory to permanently save configuration details:
```env
OPENAI_API_KEY="sk-proj-..."
# CLINIC_MODEL="gpt-4o"  # Optional: Overrides the auto-detected default model
```

**Model Auto-Detection:** The AI Critic automatically resolves the appropriate default model based on which API key is present in your environment (e.g., `OPENAI_API_KEY` defaults to `gpt-4o-mini`).
To view all supported providers and their default models, run:
```bash
workflow-clinic list-models
```

#### CLI Options
You can temporarily override the default model directly on the command line:
```bash
workflow-clinic examine main.nf --enhance --model gpt-4o --api-key sk-proj-...
```
> [!WARNING]
> **Security Notice**: Avoid passing explicit `--api-key` arguments in shared or public environments as they can leak into your shell history (`history`) or process listings (`ps aux`). Using environment variables or a `.env` file is the highly recommended security practice.

---

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

This project is licensed under the [Apache License 2.0](LICENSE).
