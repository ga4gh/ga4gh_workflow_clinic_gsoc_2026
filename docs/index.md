# Workflow Clinic

Welcome to the **Workflow Clinic** documentation!

Workflow Clinic is an AI-powered tool designed to simplify, automate, and optimize the **cloudification of bioinformatics workflows**. The project acts as an automated "clinic" that ingests scientific workflows (written in formats like Nextflow or Snakemake), analyzes their layout and tasks, and provides suggestions, cloud validation rules, and structural translations.

This initiative is part of the **GA4GH GSoC 2026** program.

---

## 🚀 Key Objectives

- **Language-Independent Representation**: Model pipelines uniformly using our structured `WorkflowBundle` schema.
- **Rule-Based Validation**: Run checks to verify compliance with cloud execution standards (such as memory limits, container registry accessibility, and parameter structures).
- **AI-Powered Refactoring**: Assist developers in migrating local/legacy scientific pipelines to modern cloud-ready environments (e.g., AWS Batch, Google Cloud Life Sciences, or Kubernetes).
- **GA4GH Standards Alignment**: Integrate validation rules referencing standard GA4GH systems (like refget, refgenie, and WES schemas).

---

## 🗺️ Project Status

We are currently building out the core models, tests, and base developer governance configurations.

1. **Repository Governance**: PR templates, Contributor Covenant Code of Conduct, and styling rules are established.
2. **Intermediate Model**: The first version of the language-independent `WorkflowBundle` Pydantic models is implemented under `src/workflow_clinic/models`.
3. **Docstrings & Documentation**: Flawless Google-style docstrings are applied across all Python source modules.

---

## 📖 Navigation

* Explore the [Architecture Overview](architecture/architecture.md) to understand the intermediate models and packages.
* Review the [Contributing Guidelines](https://github.com/ga4gh/ga4gh_workflow_clinic_gsoc_2026/blob/main/CONTRIBUTING.md) to set up your local development environment.
* View the [Code of Conduct](https://github.com/ga4gh/ga4gh_workflow_clinic_gsoc_2026/blob/main/CODE_OF_CONDUCT.md) to understand our community standards.
