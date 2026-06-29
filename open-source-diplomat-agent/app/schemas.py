"""
Pydantic schemas for the open-source-diplomat-agent workflow.

Every LLM agent node uses an output_schema so downstream function nodes
receive typed dicts instead of raw types.Content objects.
"""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Classification
# ---------------------------------------------------------------------------


class ContentKind(str, Enum):
    """Top-level classification for incoming repository content."""

    DISAGREEMENT = "disagreement"
    COMPLIANCE = "compliance"
    UNKNOWN = "unknown"


class ClassificationResult(BaseModel):
    """Output of the classifier node."""

    kind: ContentKind = Field(
        description="Whether the input is a technical disagreement or a compliance request."
    )
    confidence: float = Field(
        ge=0.0,
        le=1.0,
        description="Classifier confidence in [0, 1].",
    )
    summary: str = Field(
        description="One-sentence human-readable summary of the content."
    )
    raw_input: str = Field(
        description="The original input text passed through for downstream nodes."
    )


# ---------------------------------------------------------------------------
# Moderator (disagreement path)
# ---------------------------------------------------------------------------


class Viewpoint(BaseModel):
    """A single stakeholder's position in a technical disagreement."""

    author: str = Field(description="GitHub handle or role of the author.")
    position: str = Field(description="Their stated technical position.")
    key_arguments: list[str] = Field(description="Bullet-point arguments they raise.")
    tone: str = Field(description="Tone tag: 'neutral' | 'frustrated' | 'dismissive' | 'collaborative'.")


class ModerationOutput(BaseModel):
    """Output produced by the Moderator LLM agent."""

    viewpoints: list[Viewpoint] = Field(
        description="Parsed viewpoints from each identifiable stakeholder."
    )
    conflict_core: str = Field(
        description="One sentence naming the root technical disagreement."
    )
    compromise_proposal: str = Field(
        description="A concrete, actionable compromise proposal drafted by the moderator."
    )
    next_steps: list[str] = Field(
        description="Ordered list of recommended next steps for the maintainer."
    )


# ---------------------------------------------------------------------------
# Architectural Reviewer (compliance path)
# ---------------------------------------------------------------------------


class LintViolation(BaseModel):
    """A single linting or style violation found in the diff."""

    file: str
    line: int | None = None
    rule: str
    message: str
    severity: str = Field(description="'error' | 'warning' | 'info'")


class ComplianceOutput(BaseModel):
    """Output produced by the Architectural Reviewer LLM agent."""

    files_reviewed: list[str] = Field(description="Files examined during review.")
    violations: list[LintViolation] = Field(description="All linting/style violations found.")
    baseline_status: str = Field(
        description="'PASS' | 'FAIL' — whether the diff passes the project baseline."
    )
    architectural_notes: list[str] = Field(
        description="High-level architectural observations not captured by lint rules."
    )
    remediation_plan: str = Field(
        description="Step-by-step remediation plan for the contributor."
    )


# ---------------------------------------------------------------------------
# Human-in-the-Loop triage
# ---------------------------------------------------------------------------


class TriagePackage(BaseModel):
    """Merged payload forwarded to the HITL triage node."""

    tag: str = Field(
        default="[REQUIRES_MANUAL_REVIEW]",
        description="Fixed tag signalling manual review is required.",
    )
    source_kind: str = Field(description="'disagreement' or 'compliance'.")
    summary: str = Field(description="One-sentence summary from the classifier.")
    moderation_output: ModerationOutput | None = Field(
        default=None,
        description="Populated only for disagreement path.",
    )
    compliance_output: ComplianceOutput | None = Field(
        default=None,
        description="Populated only for compliance path.",
    )


class MaintainerDecision(BaseModel):
    """The maintainer's decision after reviewing the triage package."""

    approved: bool = Field(description="True if the maintainer approves the proposed direction.")
    comments: str = Field(default="", description="Optional free-text comments from the maintainer.")
    assignee: str = Field(default="", description="GitHub handle to assign follow-up work to.")


class FinalReport(BaseModel):
    """Final workflow output, post-maintainer approval."""

    triage: TriagePackage
    decision: MaintainerDecision
    resolution_status: str = Field(
        description="'APPROVED' | 'REJECTED' | 'NEEDS_REVISION'"
    )
