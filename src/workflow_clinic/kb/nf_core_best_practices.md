# Nextflow and nf-core Best Practices

Nextflow is a dataflow-based workflow language designed to make data-intensive pipelines portable across execution environments — local machines, HPC schedulers (SLURM, LSF, PBS, HTCondor), and cloud platforms (AWS, Google Cloud, Kubernetes) — without changing the pipeline code itself. A workflow's processes (units of work) and channels (the asynchronous queues connecting them) are meant to be declared independently of any specific execution environment; anything that ties a process to one particular machine or filesystem layout undermines that portability.

<!-- rule: W002 -->
## Why Resource Declarations Matter

Nextflow's portability across executors depends on each process declaring what it needs, not on the executor guessing. When a process omits cpus or memory, a scheduler like AWS Batch or Google Cloud Batch has no basis to size the compute resource it launches — it will either fall back to an overly conservative default (slowing the pipeline down) or fail to schedule the task at all. Declaring resources explicitly is what allows the same pipeline definition to run efficiently on a laptop, an HPC cluster, and a cloud batch service.

```groovy
process ALIGN_READS {
    cpus 8
    memory '16 GB'

    script:
    """
    bwa mem -t ${task.cpus} reference.fa reads.fq > out.sam
    """
}
```

<!-- rule: W002 -->
## Dynamic Resource Scaling

Because Nextflow tracks every process execution and can resume from checkpoints, it's common practice to let resource requests grow on retry rather than hardcoding a single fixed value that might be too small for larger input files:

```groovy
memory { 8.GB * task.attempt }
maxRetries 3
errorStrategy 'retry'
```

This pattern only works if memory/cpus are declared as expressions in the first place — a hardcoded literal value has no `task.attempt` to scale against, and the retry-with-more-resources strategy silently does nothing.

<!-- rule: global -->
## Reproducibility Through Containers

Nextflow has built-in support for Docker and Singularity/Apptainer containers, which is what allows a pipeline shared on GitHub to be pulled down and executed by someone else with the exact same software environment, rather than relying on whatever happens to already be installed on their machine. Combining explicit resource declarations with pinned containers is what actually makes a pipeline reproducible end-to-end — resource limits alone don't guarantee the same software runs, and pinned containers alone don't guarantee the scheduler can allocate the job correctly.

<!-- rule: global -->
## Independent, Composable Processes

A Nextflow pipeline is structured as a graph: processes are nodes, channels are the edges connecting their inputs and outputs. Processes that don't depend on each other's outputs can run in parallel automatically — there is no manual parallelization code to write, but this only holds if a process's script is genuinely self-contained and does not assume some other process has already run and left files in a particular local location outside of the declared channel inputs.
