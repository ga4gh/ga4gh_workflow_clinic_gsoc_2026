# Cloud Executor Portability

Nextflow supports many execution backends — including local execution, traditional HPC schedulers (SLURM, LSF, PBS/Torque, SGE, HTCondor), and cloud-native executors (AWS Batch, Google Cloud Batch, Kubernetes) — through the same pipeline code. Switching backends is meant to be a configuration change, not a rewrite. A profile in nextflow.config is how that backend-specific configuration is isolated from the pipeline logic itself.

<!-- rule: global -->
## AWS Batch Profile Shape

An AWS Batch profile needs to tell Nextflow which executor to use and which compute queue to submit to; everything else (instance types, scaling) is typically managed by the AWS Batch compute environment itself, not the pipeline:

```groovy
profiles {
  awsbatch {
    process.executor = 'awsbatch'
    process.queue = 'my-batch-queue'
    aws.region = 'us-east-1'
    workDir = 's3://my-bucket/work'
  }
}
```

The work directory must point to object storage (e.g. S3) reachable by the Batch compute environment — a local or NFS path will not be visible to the remote workers.

<!-- rule: global -->
## Google Cloud Batch / Life Sciences Profile Shape

Google Cloud's batch executors follow the same pattern: declare the executor, then supply the project and region Nextflow should submit jobs into:

```groovy
profiles {
  gcb {
    process.executor = 'google-batch'
    google.project = 'my-gcp-project'
    google.location = 'us-central1'
    workDir = 'gs://my-bucket/work'
  }
}
```

Older pipelines may reference `google-lifesciences` instead of `google-batch` — both exist because Google Cloud Life Sciences was the predecessor API; new pipelines should prefer `google-batch`.

<!-- rule: global -->
## Why Local-Only Assumptions Break in the Cloud

Workflows written only for a shared local filesystem or a specific scheduler's flags (for example, hardcoding `clusterOptions = '-p some_local_partition'` for SLURM) fail outright when pointed at a cloud executor, because cloud compute nodes generally do not share a common filesystem the way an HPC cluster's nodes do, and cloud schedulers don't recognize on-prem scheduler flags. Preferring abstract resource requests (cpus, memory, time) over executor-specific flags is what lets the same process definition run unmodified on a local machine, an HPC cluster, or a cloud batch service — the executor translates the abstract request into whatever its underlying platform requires.
