#!/usr/bin/env nextflow

/*
 * dummy.nf — Minimal but realistic DSL2 workflow used as a parser test fixture.
 * Mirrors common nf-core patterns: container per process, tag, publishDir,
 * resource directives, and a channel-driven main workflow block.
 */

nextflow.enable.dsl = 2

params.reads   = "$baseDir/data/*_R{1,2}.fastq.gz"
params.outdir  = "results"

process FASTQC {
    tag "FASTQC on $sample_id"
    container "quay.io/biocontainers/fastqc:0.12.1--hdfd78af_0"
    cpus 2
    memory { 4.GB * task.attempt }
    publishDir "${params.outdir}/fastqc", mode: "copy"

    input:
    tuple val(sample_id), path(reads)

    output:
    path "*.zip", emit: zip
    path "*.html", emit: html

    script:
    """
    fastqc --threads ${task.cpus} ${reads}
    """
}

process TRIM_READS {
    tag "Trimming $sample_id"
    container "quay.io/biocontainers/trim-galore:0.6.10--hdfd78af_0"
    cpus 4
    memory "8 GB"
    publishDir "${params.outdir}/trimmed", mode: "copy"

    input:
    tuple val(sample_id), path(reads)

    output:
    tuple val(sample_id), path("*_trimmed.fq.gz"), emit: trimmed_reads
    path "*_trimming_report.txt", emit: report

    script:
    """
    trim_galore --paired --gzip --cores ${task.cpus} ${reads}
    """
}

process ALIGN {
    tag "Aligning $sample_id"
    container "quay.io/biocontainers/bwa:0.7.17--hed695b0_7"
    cpus 8
    memory "16 GB"
    publishDir "${params.outdir}/aligned", mode: "copy"

    input:
    tuple val(sample_id), path(reads)
    path reference

    output:
    tuple val(sample_id), path("*.bam"), emit: bam

    script:
    """
    bwa mem -t ${task.cpus} ${reference} ${reads} | samtools sort -o ${sample_id}.bam
    """
}

workflow {
    read_pairs_ch = Channel
        .fromFilePairs(params.reads, checkIfExists: true)

    FASTQC(read_pairs_ch)
    TRIM_READS(read_pairs_ch)
    ALIGN(TRIM_READS.out.trimmed_reads, file(params.reference))
}
