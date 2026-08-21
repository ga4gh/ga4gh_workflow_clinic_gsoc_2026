"""Unit tests for Layer 2 Regex Fixers (W003 Path Parameterization and W004 Credential Masking)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from workflow_clinic.doctor.fixers.credentials import CredentialRegexFixer
from workflow_clinic.doctor.fixers.paths import PathRegexFixer, path_to_param_name
from workflow_clinic.models.diagnosis import Finding
from workflow_clinic.models.fix import FixStrategyLayer

if TYPE_CHECKING:
    from pathlib import Path


def test_path_to_param_name_standard() -> None:
    """Verify standard absolute paths generate clean parameter names."""
    assert path_to_param_name("/data/genomes/hg38.fa") == "params.hg38"
    assert path_to_param_name("/opt/tools/bwa-mem2.exe") == "params.bwa_mem2"
    assert path_to_param_name("/ref/annotation_v2.gtf") == "params.annotation_v2"


def test_path_to_param_name_generic_fallbacks() -> None:
    """Verify generic/ambiguous file stems are enriched with parent directory context."""
    assert path_to_param_name("/home/user/inputs/input.fastq") == "params.inputs_input"
    assert path_to_param_name("/var/tmp/results/output.tsv") == "params.results_output"  # noqa: S108
    assert path_to_param_name("/data/samples/data.csv") == "params.samples_data"
    assert path_to_param_name("/tmp/results") == "params.tmp_results"  # noqa: S108


def test_path_to_param_name_special_characters() -> None:
    """Verify paths with special symbols, dashes, and dots are sanitized cleanly."""
    assert (
        path_to_param_name("/storage/my-sample-v1.0.tar.gz")
        == "params.my_sample_v1_0_tar"
    )
    assert (
        path_to_param_name("/data/10x_genomics/GRCh38-2020-A") == "params.grch38_2020_a"
    )


def test_path_regex_fixer_input_directive() -> None:
    """Verify PathRegexFixer replaces hardcoded path inside an input: directive context."""
    fixer = PathRegexFixer()
    assert fixer.strategy_layer == FixStrategyLayer.LAYER2_REGEX

    finding = Finding(
        rule_id="W003",
        severity="warning",
        message="Process 'ALIGN' contains hardcoded absolute path: '/data/genomes/hg38.fa'",
        file_path="main.nf",
        line_number=3,
        process_name="ALIGN",
    )
    code = """process ALIGN {
    input:
    path genome from file('/data/genomes/hg38.fa')

    script:
    \"\"\"
    bwa mem genome reads.fq
    \"\"\"
}"""
    proposal = fixer.generate_proposal(finding, code)
    assert proposal is not None
    assert proposal.strategy_layer == FixStrategyLayer.LAYER2_REGEX
    assert "file(params.hg38)" in proposal.proposed_snippet
    assert "params.hg38" in proposal.explanation


def test_path_regex_fixer_script_body() -> None:
    """Verify PathRegexFixer replaces hardcoded path inside a script: body context."""
    fixer = PathRegexFixer()
    finding = Finding(
        rule_id="W003",
        severity="warning",
        message="Process 'FILTER' contains hardcoded absolute path: '/opt/bin/filter_tool'",
        file_path="main.nf",
        line_number=4,
        process_name="FILTER",
    )
    code = """process FILTER {
    script:
    \"\"\"
    '/opt/bin/filter_tool' --input sample.tsv
    \"\"\"
}"""
    proposal = fixer.generate_proposal(finding, code)
    assert proposal is not None
    assert "params.filter_tool --input sample.tsv" in proposal.proposed_snippet


def test_path_regex_fixer_process_scoping() -> None:
    """Verify PathRegexFixer scopes replacement strictly to the target process when process_name is present."""
    fixer = PathRegexFixer()
    finding = Finding(
        rule_id="W003",
        severity="warning",
        message="Process 'PROC_A' contains '/data/shared.fa'",
        file_path="main.nf",
        process_name="PROC_A",
    )
    code = """process PROC_A {
    script: "toolA '/data/shared.fa'"
}

process PROC_B {
    script: "toolB '/data/shared.fa'"
}"""
    proposal = fixer.generate_proposal(finding, code)
    assert proposal is not None
    assert "toolA params.shared" in proposal.proposed_snippet
    # PROC_B should NOT be modified because the finding is scoped to PROC_A
    assert "toolB '/data/shared.fa'" in proposal.proposed_snippet


def test_path_regex_fixer_multi_occurrence() -> None:
    """Verify PathRegexFixer replaces all occurrences when no process scope is specified."""
    fixer = PathRegexFixer()
    finding = Finding(
        rule_id="W003",
        severity="warning",
        message="Hardcoded path: '/ref/shared.fa'",
        file_path="pipeline.nf",
        line_number=2,
    )
    code = """process STEP1 {
    script: "toolA '/ref/shared.fa'"
}

process STEP2 {
    script: "toolB '/ref/shared.fa'"
}"""
    proposal = fixer.generate_proposal(finding, code)
    assert proposal is not None
    assert "toolA params.shared" in proposal.proposed_snippet
    assert "toolB params.shared" in proposal.proposed_snippet
    assert "'/ref/shared.fa'" not in proposal.proposed_snippet


def test_path_regex_fixer_apply_disk(tmp_path: Path) -> None:
    """Verify PathRegexFixer modifies file on disk cleanly via apply_fix."""
    target_file = tmp_path / "workflow.nf"
    target_file.write_text("ref = '/data/ref.fa'\n", encoding="utf-8")

    fixer = PathRegexFixer()
    finding = Finding(
        rule_id="W003",
        severity="warning",
        message="Found '/data/ref.fa'",
        file_path="workflow.nf",
    )
    prop = fixer.generate_proposal(finding, target_file.read_text(encoding="utf-8"))
    assert prop is not None

    outcome = fixer.apply_fix(prop, root_dir=tmp_path)
    assert outcome.success is True
    assert target_file.read_text(encoding="utf-8") == "ref = params.ref\n"


def test_credential_regex_fixer_aws_key() -> None:
    """Verify CredentialRegexFixer replaces AWS access key with parameter and TODO comment."""
    fixer = CredentialRegexFixer()
    assert fixer.strategy_layer == FixStrategyLayer.LAYER2_REGEX

    finding = Finding(
        rule_id="W004",
        severity="error",
        message="Process 'DOWNLOAD' contains hardcoded AWS Access Key",
        file_path="main.nf",
        process_name="DOWNLOAD",
    )
    code = """process DOWNLOAD {
    script:
    \"\"\"
    export AWS_ACCESS_KEY_ID="AKIAIOSFODNN7EXAMPLE"
    aws s3 cp s3://bucket/data.bam .
    \"\"\"
}"""
    proposal = fixer.generate_proposal(finding, code)
    assert proposal is not None
    assert proposal.strategy_layer == FixStrategyLayer.LAYER2_REGEX
    assert "params.aws_access_key" in proposal.proposed_snippet
    assert "AKIAIOSFODNN7EXAMPLE" not in proposal.proposed_snippet
    assert "Rotate" in proposal.proposed_snippet or "Rotate" in proposal.explanation


def test_credential_regex_fixer_github_token() -> None:
    """Verify CredentialRegexFixer replaces GitHub PAT with parameter."""
    fixer = CredentialRegexFixer()
    finding = Finding(
        rule_id="W004",
        severity="error",
        message="Hardcoded GitHub PAT in 'SYNC'",
        file_path="main.nf",
        process_name="SYNC",
    )
    code = """process SYNC {
    script: "curl -H 'Authorization: token ghp_1234567890abcdef1234567890abcdef1234' https://api.github.com"
}"""
    proposal = fixer.generate_proposal(finding, code)
    assert proposal is not None
    assert "params.github_token" in proposal.proposed_snippet
    assert "ghp_1234567890abcdef1234567890abcdef1234" not in proposal.proposed_snippet


def test_credential_regex_fixer_generic_assignment() -> None:
    """Verify CredentialRegexFixer replaces generic high-entropy secret assignment."""
    fixer = CredentialRegexFixer()
    finding = Finding(
        rule_id="W004",
        severity="warning",
        message="Generic secret assignment in 'AUTH'",
        file_path="main.nf",
        process_name="AUTH",
    )
    code = """process AUTH {
    script:
    \"\"\"
    api_key = "d8f3k9sl20fk4ls02kf4"
    ./tool --key $api_key
    \"\"\"
}"""
    proposal = fixer.generate_proposal(finding, code)
    assert proposal is not None
    assert "api_key = params.api_key" in proposal.proposed_snippet
    assert "TODO: Rotate this credential" in proposal.proposed_snippet
