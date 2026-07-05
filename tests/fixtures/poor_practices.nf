#!/usr/bin/env nextflow

/*
 * poor_practices.nf — Deliberately flawed DSL2 workflow for rule engine testing.
 * Each process violates at least one best practice that the Rule Engine should flag.
 */

nextflow.enable.dsl = 2

params.reads  = "$baseDir/data/*_R{1,2}.fastq.gz"
params.outdir = "results"

process NO_CONTAINER {
    cpus 1
    memory "4 GB"

    input:
    tuple val(sample_id), path(reads)

    output:
    path "*.txt"

    script:
    """
    echo "Running without a container" > output.txt
    """
}

process UNPINNED_TAG {
    container "ubuntu:latest"
    cpus 4
    memory "8 GB"

    input:
    tuple val(sample_id), path(reads)

    output:
    path "*.bam"

    script:
    """
    echo "Using unpinned container tag" > output.bam
    """
}

process NO_RESOURCES {
    container "quay.io/biocontainers/samtools:1.17--hd87286a_2"

    input:
    tuple val(sample_id), path(reads)

    output:
    path "*.vcf"

    script:
    """
    echo "No CPU or memory declared" > output.vcf
    """
}

process TAGLESS_IMAGE {
    container "ubuntu"
    cpus 2
    memory "4 GB"

    input:
    path reference

    output:
    path "*.log"

    script:
    """
    echo "Container has no tag at all" > output.log
    """
}

workflow {
    read_pairs_ch = Channel
        .fromFilePairs(params.reads, checkIfExists: true)

    NO_CONTAINER(read_pairs_ch)
    UNPINNED_TAG(read_pairs_ch)
    NO_RESOURCES(read_pairs_ch)
    TAGLESS_IMAGE(file(params.outdir))
}
