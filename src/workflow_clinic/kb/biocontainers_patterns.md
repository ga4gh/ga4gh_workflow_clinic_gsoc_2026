# BioContainers Patterns

BioContainers is a community project that packages bioinformatics tools into standardized, versioned container images, built automatically from Bioconda recipes and published to Quay.io. Using these images instead of ad hoc or locally-built ones gives a workflow the same reproducibility guarantees the wider bioinformatics community already relies on.

<!-- rule: W001 -->
## Why Unpinned Containers Break Reproducibility

A container reference without an explicit version — either a bare image name or an image tagged :latest — resolves to whatever the registry currently considers "latest" at pull time. Because that mapping can change whenever the upstream image is rebuilt, two runs of the same workflow, weeks apart, can silently execute different tool versions. This defeats the entire purpose of containerizing a pipeline: the point of a container is to fix the software environment, not just to fix that a container is used.

Avoid:
```groovy
container 'ubuntu'          // no tag — resolves to :latest
container 'ubuntu:latest'   // floating tag, not reproducible
```

Prefer:
```groovy
container 'quay.io/biocontainers/fastqc:0.12.1--hdfd78af_0'
```

<!-- rule: W001 -->
## Pinning by Digest for Stronger Guarantees

A tag is a mutable pointer — even a specific version tag could technically be re-pushed by a registry admin. A content digest (@sha256:...) instead references the exact bytes of an image, so it cannot silently change even if the tag is reused. For workflows where byte-for-byte reproducibility matters (e.g. results that will be published or audited), pinning by digest is stronger than pinning by tag alone:

```groovy
container 'quay.io/biocontainers/samtools@sha256:aabb7783a654d0085a6a68f000bb7ccba88d8b945d8b76712ef3c05af4021a8e'
```

A digest reference should always be treated as fully pinned, regardless of whether a human-readable tag is present alongside it.

<!-- rule: global -->
## BioContainers Naming Conventions

BioContainers images published on Quay.io generally follow the pattern:

`quay.io/biocontainers/<tool>:<version>--<build_string>`

The `<build_string>` suffix (for example `hdfd78af_0`) identifies the exact Bioconda build recipe and environment used to construct the image. It is not decorative — two images with the same tool and version but different build strings can differ in their transitive dependencies. Preserve the full build string when copying a container reference between workflows or configuration files rather than truncating it to just `<tool>:<version>`.

<!-- rule: global -->
## Preferring Community Images Over Ad Hoc Ones

Where a BioContainers image already exists for a tool, it is generally preferable to a personal Docker Hub image or a locally built one: it is version-pinned by convention, built from an auditable Bioconda recipe, and already used across many published pipelines, which makes issues more likely to be caught and fixed upstream rather than silently affecting only one workflow.
