#!/usr/bin/env nextflow

/*
 * hardcoded_paths.nf — Test fixture for W003 (Hardcoded Path rule).
 *
 * Contains processes designed to test absolute path detection:
 *  - USES_HARDCODED_PATH: should trigger W003 (real absolute path)
 *  - USES_STANDARD_SHELL_PATHS: should NOT trigger (excluded paths)
 *  - USES_RELATIVE_PATH: should NOT trigger (not absolute)
 *  - USES_URL_REFERENCE: should NOT trigger (URL, not a path)
 */

nextflow.enable.dsl = 2

process USES_HARDCODED_PATH {
    container "quay.io/biocontainers/samtools:1.17--hd87286a_2"
    cpus 2
    memory "4 GB"

    script:
    """
    samtools index /home/user/data/aligned.bam
    """
}

process USES_STANDARD_SHELL_PATHS {
    container "quay.io/biocontainers/samtools:1.17--hd87286a_2"
    cpus 2
    memory "4 GB"

    script:
    """
    #!/bin/bash
    samtools view input.bam > /dev/stdout
    """
}

process USES_RELATIVE_PATH {
    container "quay.io/biocontainers/samtools:1.17--hd87286a_2"
    cpus 2
    memory "4 GB"

    script:
    """
    samtools sort ./data/input.bam -o output.bam
    """
}

process USES_URL_REFERENCE {
    container "quay.io/biocontainers/samtools:1.17--hd87286a_2"
    cpus 2
    memory "4 GB"

    script:
    """
    curl -O https://example.com/reference/genome.fa
    """
}
