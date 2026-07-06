# GA4GH Standards Alignment

The Global Alliance for Genomics and Health (GA4GH) is a non-profit international alliance that develops technical standards and policy frameworks to make genomic and related health data usable and shareable across institutions, without every organization having to independently reinvent how to expose, discover, or execute that data and the tools that operate on it. GA4GH standards are developed openly by Work Streams and adopted by "Driver Projects" — real genomic initiatives that pilot the standards in production — before being formally approved as GA4GH products.

<!-- rule: global -->
## Tool Registry Service (TRS)

The GA4GH Tool Registry Service (TRS) API standardizes how workflows and tools are described and discovered across independent registries. Platforms like WorkflowHub and Dockstore both expose a TRS-compatible API, which is what allows a workflow published on one registry to be discovered, described, and referenced consistently by any other TRS-aware tool, without that tool needing custom integration code per registry. A workflow that wants to be TRS-discoverable needs consistent identifying metadata — name, version, description, author — since the TRS API indexes and serves workflows based on exactly these fields.

<!-- rule: global -->
## Workflow Execution Service (WES)

The GA4GH Workflow Execution Service (WES) API standardizes how a workflow (written in Nextflow, WDL, CWL, or Snakemake) is submitted for execution across different compute environments, independent of which engine ultimately runs it. Because a WES-compliant submission may run on infrastructure the workflow's author has no direct access to, the workflow must be self-contained: every input file the workflow needs must be resolvable from a remote location (object storage such as S3/GCS, or a versioned repository such as GitHub) rather than a hardcoded local path on the author's own machine. A workflow that depends on an absolute path like `/home/user/data/reference.fa` cannot be executed remotely through WES, because that path simply does not exist on the execution backend.

<!-- rule: global -->
## Why This Matters for Portability Checks

The properties GA4GH's registry and execution standards actually require — pinned, discoverable tool versions; no hardcoded local paths; explicit resource declarations — are the same properties that make a workflow portable to any cloud environment in the first place. A workflow that passes basic cloud-readiness checks (pinned containers, declared resources, no absolute local paths) is, not coincidentally, also much closer to being TRS- and WES-compliant than one that doesn't.
