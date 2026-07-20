process USES_AWS_KEY {
    container "quay.io/biocontainers/samtools:1.17--hd87286a_2"
    cpus 2
    memory "4 GB"

    script:
    """
    export AWS_ACCESS_KEY_ID=AKIAIOSFODNN7EXAMPLE
    samtools index aligned.bam
    """
}

process USES_GENERIC_HIGH_ENTROPY_SECRET {
    container "quay.io/biocontainers/samtools:1.17--hd87286a_2"
    cpus 2
    memory "4 GB"

    script:
    """
    api_key="aK9x!mQ7zP2sW8vB4nR6tY1c"
    curl -H "Authorization: Bearer $api_key" https://api.example.com
    """
}

process USES_PLACEHOLDER_ONLY {
    container "quay.io/biocontainers/samtools:1.17--hd87286a_2"
    cpus 2
    memory "4 GB"

    script:
    """
    password="YOUR_PASSWORD_HERE"
    echo "configure password before running"
    """
}

process USES_CONTAINER_REFERENCE_ONLY {
    container "quay.io/biocontainers/samtools@sha256:7783a654d0085a6a68f000bb7ccba88d8b945d8b767"
    cpus 2
    memory "4 GB"

    script:
    """
    samtools view input.bam
    """
}
