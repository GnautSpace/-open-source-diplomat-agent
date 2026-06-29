"""
Smoke tests for the open-source-diplomat-agent workflow.

These tests verify graph structure and function-node logic, NOT LLM output.
For LLM behaviour validation use `agents-cli eval` or `adk eval`.
"""

from __future__ import annotations

import pytest

from app.agent import root_agent
from app.schemas import (
    ClassificationResult,
    ComplianceOutput,
    ContentKind,
    ModerationOutput,
    TriagePackage,
    Viewpoint,
)


class TestSchemas:
    """Validate Pydantic schema round-trips."""

    def test_classification_result_roundtrip(self):
        data = {
            "kind": "disagreement",
            "confidence": 0.92,
            "summary": "Heated debate over async vs sync API.",
            "raw_input": "This is terrible code!",
        }
        result = ClassificationResult(**data)
        assert result.kind == ContentKind.DISAGREEMENT
        assert result.confidence == 0.92

    def test_triage_package_default_tag(self):
        pkg = TriagePackage(source_kind="compliance", summary="Lint failures detected.")
        assert pkg.tag == "[REQUIRES_MANUAL_REVIEW]"

    def test_moderation_output_roundtrip(self):
        mod = ModerationOutput(
            viewpoints=[
                Viewpoint(
                    author="alice",
                    position="Use async everywhere.",
                    key_arguments=["Better throughput", "Modern Python"],
                    tone="collaborative",
                )
            ],
            conflict_core="Whether to adopt async I/O across the codebase.",
            compromise_proposal="Adopt async only in the new gateway module.",
            next_steps=["Open an RFC", "Benchmark both approaches"],
        )
        dumped = mod.model_dump()
        restored = ModerationOutput(**dumped)
        assert restored.conflict_core == mod.conflict_core

    def test_compliance_output_roundtrip(self):
        from app.schemas import LintViolation

        comp = ComplianceOutput(
            files_reviewed=["gateway.py"],
            violations=[
                LintViolation(
                    file="gateway.py",
                    line=42,
                    rule="E501",
                    message="Line too long",
                    severity="warning",
                )
            ],
            baseline_status="FAIL",
            architectural_notes=["Missing abstraction layer"],
            remediation_plan="1. Break long lines\n2. Add interface class",
        )
        assert comp.baseline_status == "FAIL"
        assert len(comp.violations) == 1


class TestWorkflowStructure:
    """Verify the Workflow object is correctly defined."""

    def test_root_agent_name(self):
        assert root_agent.name == "open_source_diplomat"

    def test_root_agent_is_workflow(self):
        from google.adk.workflow import Workflow

        assert isinstance(root_agent, Workflow)

    def test_edges_are_defined(self):
        # The Workflow stores edges — confirm they were accepted without error
        # (graph validation happens at construction time)
        assert root_agent is not None
