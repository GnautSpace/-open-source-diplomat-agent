"""
open-source-diplomat-agent — ADK 2.0 Graph Workflow
====================================================

Graph topology
--------------

  START
    │
    ▼
  classify_input  ──(disagreement)──►  moderator_agent
                  ──(compliance)────►  architectural_reviewer
                  ──(unknown)───────►  human_triage          (direct pass-through)
                                            ▲
  moderator_agent ─────────────────────────►│
  architectural_reviewer ──────────────────►│
                                            │
                                      human_triage
                                       (HITL node)
                                            │
                                       ▼ (on approval)
                                    finalize_report

Node descriptions
-----------------
- ``classify_input``       : Function node — calls the Classifier LLM agent
                             and routes to the correct processing branch.
- ``moderator_agent``      : LlmAgent — parses conflicting viewpoints and
                             drafts a compromise proposal.
- ``architectural_reviewer``: LlmAgent — lints code diffs and produces a
                             compliance report (has code-execution tools).
- ``human_triage``         : Async generator function node — tags output with
                             [REQUIRES_MANUAL_REVIEW], emits a RequestInput
                             interrupt, and waits for a maintainer decision.
- ``finalize_report``      : Function node — assembles the FinalReport and
                             emits a content event for the web UI.
"""

from __future__ import annotations

import json
from typing import Any

from google.adk.agents import LlmAgent
from google.adk.agents.context import Context
from google.adk.events.event import Event
from google.adk.events.request_input import RequestInput
from google.adk.workflow import Workflow
from google.genai import types

from app.prompts import (
    ARCHITECTURAL_REVIEWER_INSTRUCTION,
    CLASSIFIER_INSTRUCTION,
    MODERATOR_INSTRUCTION,
)
from app.schemas import (
    ClassificationResult,
    ComplianceOutput,
    ContentKind,
    FinalReport,
    MaintainerDecision,
    ModerationOutput,
    TriagePackage,
)
from app.tools import REVIEWER_TOOLS

# ---------------------------------------------------------------------------
# Model selection
# ---------------------------------------------------------------------------

_MODEL = "gemini-2.5-flash"   # Latest Flash — best latency/cost for workflow nodes

# ---------------------------------------------------------------------------
# LLM Agent Nodes
# ---------------------------------------------------------------------------

classifier_agent = LlmAgent(
    name="classifier_agent",
    model=_MODEL,
    instruction=CLASSIFIER_INSTRUCTION,
    output_schema=ClassificationResult,
    output_key="classification",
    description="Classifies incoming repository content as a technical disagreement or compliance request.",
)

moderator_agent = LlmAgent(
    name="moderator_agent",
    model=_MODEL,
    instruction=MODERATOR_INSTRUCTION,
    output_schema=ModerationOutput,
    output_key="moderation",
    description=(
        "Neutral mediator: parses conflicting technical viewpoints into a "
        "structured JSON matrix and drafts a compromise proposal."
    ),
)

architectural_reviewer = LlmAgent(
    name="architectural_reviewer",
    model=_MODEL,
    instruction=ARCHITECTURAL_REVIEWER_INSTRUCTION,
    tools=REVIEWER_TOOLS,
    output_schema=ComplianceOutput,
    output_key="compliance",
    description=(
        "Senior architect reviewer: runs linting tools, verifies code baselines, "
        "and produces a structured compliance report."
    ),
)

# ---------------------------------------------------------------------------
# Function Nodes
# ---------------------------------------------------------------------------


def classify_input(ctx: Context, node_input: Any) -> Event:
    """
    Bridges START → classifier_agent → routing.

    The raw text from START (types.Content) is stored in state so that
    downstream LLM agents can retrieve it via output_key references.
    Then the classifier result drives conditional routing.
    """
    # Pull the classification result stored by classifier_agent's output_key
    raw_classification: dict = ctx.state.get("classification", {})

    try:
        result = ClassificationResult(**raw_classification)
    except Exception:
        # Graceful degradation: unknown content goes straight to triage
        return Event(
            output=TriagePackage(
                tag="[REQUIRES_MANUAL_REVIEW]",
                source_kind=ContentKind.UNKNOWN,
                summary="Could not classify input — routing directly to human triage.",
            ).model_dump(),
            route=ContentKind.UNKNOWN,
            state={"triage_package": TriagePackage(
                tag="[REQUIRES_MANUAL_REVIEW]",
                source_kind=ContentKind.UNKNOWN,
                summary="Classification failed.",
            ).model_dump()},
        )

    # Store raw input text for LLM agent nodes that need it
    raw_text = ""
    if hasattr(node_input, "parts"):
        raw_text = " ".join(p.text for p in node_input.parts if hasattr(p, "text"))
    elif isinstance(node_input, str):
        raw_text = node_input

    return Event(
        output=raw_text,
        route=result.kind.value,
        state={
            "source_kind": result.kind.value,
            "input_summary": result.summary,
            "raw_input": raw_text,
        },
    )


def prepare_moderation_triage(ctx: Context, node_input: Any) -> Event:
    """Packages moderation output into a TriagePackage for the HITL node."""
    raw_mod: dict = ctx.state.get("moderation", {})
    try:
        mod = ModerationOutput(**raw_mod)
    except Exception:
        mod = None

    pkg = TriagePackage(
        tag="[REQUIRES_MANUAL_REVIEW]",
        source_kind="disagreement",
        summary=ctx.state.get("input_summary", "Technical disagreement detected."),
        moderation_output=mod,
    )
    return Event(
        output=pkg.model_dump(),
        state={"triage_package": pkg.model_dump()},
    )


def prepare_compliance_triage(ctx: Context, node_input: Any) -> Event:
    """Packages compliance output into a TriagePackage for the HITL node."""
    raw_comp: dict = ctx.state.get("compliance", {})
    try:
        comp = ComplianceOutput(**raw_comp)
    except Exception:
        comp = None

    pkg = TriagePackage(
        tag="[REQUIRES_MANUAL_REVIEW]",
        source_kind="compliance",
        summary=ctx.state.get("input_summary", "Code compliance evaluation requested."),
        compliance_output=comp,
    )
    return Event(
        output=pkg.model_dump(),
        state={"triage_package": pkg.model_dump()},
    )


async def human_triage(ctx: Context, node_input: Any):
    """
    Human-in-the-Loop triage node.

    Behaviour
    ---------
    1. On first execution: emits a formatted review packet tagged with
       [REQUIRES_MANUAL_REVIEW] and yields a RequestInput interrupt,
       halting the workflow until a core maintainer responds.
    2. On resume: reads the maintainer's decision from ctx.resume_inputs
       and passes it downstream.

    The interrupt_id is fixed ("maintainer_approval") so the runner can
    reliably resume with the correct key.
    """
    INTERRUPT_ID = "maintainer_approval"

    # ── First pass: present the triage package and wait ──────────────────
    if INTERRUPT_ID not in (ctx.resume_inputs or {}):
        raw_pkg = ctx.state.get("triage_package") or node_input
        if isinstance(raw_pkg, dict):
            pkg = TriagePackage(**raw_pkg)
        else:
            pkg = TriagePackage(
                tag="[REQUIRES_MANUAL_REVIEW]",
                source_kind="unknown",
                summary=str(raw_pkg),
            )

        # Render a human-readable review packet for the web UI
        review_text = _format_review_packet(pkg)

        yield Event(
            content=types.Content(
                role="model",
                parts=[types.Part.from_text(text=review_text)],
            )
        )

        # Halt and wait for maintainer input
        yield RequestInput(
            interrupt_id=INTERRUPT_ID,
            message=(
                "⏸️  **[REQUIRES_MANUAL_REVIEW]**\n\n"
                "A core project maintainer must review the analysis above and "
                "explicitly approve the proposed direction before execution continues.\n\n"
                "Please respond with a JSON object:\n"
                "```json\n"
                '{"approved": true|false, "comments": "...", "assignee": "github-handle"}\n'
                "```"
            ),
        )
        return  # Suspend — workflow resumes when maintainer responds

    # ── Resume pass: parse maintainer decision ────────────────────────────
    raw_decision = ctx.resume_inputs[INTERRUPT_ID]
    if isinstance(raw_decision, str):
        try:
            decision_dict = json.loads(raw_decision)
        except json.JSONDecodeError:
            # Accept plain text approval
            decision_dict = {
                "approved": raw_decision.strip().lower() in ("yes", "approve", "approved", "true", "1"),
                "comments": raw_decision,
                "assignee": "",
            }
    elif isinstance(raw_decision, dict):
        decision_dict = raw_decision
    else:
        decision_dict = {"approved": False, "comments": str(raw_decision), "assignee": ""}

    decision = MaintainerDecision(**decision_dict)

    raw_pkg = ctx.state.get("triage_package", {})
    pkg = TriagePackage(**raw_pkg) if isinstance(raw_pkg, dict) and raw_pkg else TriagePackage(
        tag="[REQUIRES_MANUAL_REVIEW]", source_kind="unknown", summary="Unknown"
    )

    yield Event(
        output={"triage_package": pkg.model_dump(), "decision": decision.model_dump()},
        state={"maintainer_decision": decision.model_dump()},
    )


def finalize_report(ctx: Context, node_input: Any) -> Event:
    """
    Assembles the FinalReport from state and emits it for the web UI.
    """
    raw = node_input if isinstance(node_input, dict) else {}
    triage_dict = raw.get("triage_package") or ctx.state.get("triage_package", {})
    decision_dict = raw.get("decision") or ctx.state.get("maintainer_decision", {})

    try:
        triage = TriagePackage(**triage_dict)
    except Exception:
        triage = TriagePackage(tag="[REQUIRES_MANUAL_REVIEW]", source_kind="unknown", summary="N/A")

    try:
        decision = MaintainerDecision(**decision_dict)
    except Exception:
        decision = MaintainerDecision(approved=False, comments="Could not parse decision.")

    if decision.approved:
        status = "APPROVED"
    else:
        status = "REJECTED" if not decision.comments else "NEEDS_REVISION"

    report = FinalReport(
        triage=triage,
        decision=decision,
        resolution_status=status,
    )

    report_text = _format_final_report(report)

    return Event(
        output=report.model_dump(),
        content=types.Content(
            role="model",
            parts=[types.Part.from_text(text=report_text)],
        ),
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _format_review_packet(pkg: TriagePackage) -> str:
    """Render a triage package as a markdown string for the web UI."""
    lines = [
        f"## {pkg.tag}",
        f"**Type:** `{pkg.source_kind}`",
        f"**Summary:** {pkg.summary}",
        "",
    ]

    if pkg.moderation_output:
        m = pkg.moderation_output
        lines += [
            "### 🔥 Detected Technical Disagreement",
            f"**Core conflict:** {m.conflict_core}",
            "",
            "**Stakeholder Viewpoints:**",
        ]
        for vp in m.viewpoints:
            lines.append(f"- **{vp.author}** ({vp.tone}): {vp.position}")
            for arg in vp.key_arguments:
                lines.append(f"  - {arg}")
        lines += [
            "",
            f"**💡 Compromise Proposal:** {m.compromise_proposal}",
            "",
            "**Recommended Next Steps:**",
        ]
        for i, step in enumerate(m.next_steps, 1):
            lines.append(f"{i}. {step}")

    if pkg.compliance_output:
        c = pkg.compliance_output
        lines += [
            "### 🔍 Code Compliance Review",
            f"**Baseline Status:** `{c.baseline_status}`",
            f"**Files Reviewed:** {', '.join(c.files_reviewed) or 'N/A'}",
            "",
            "**Violations:**",
        ]
        if c.violations:
            for v in c.violations:
                loc = f":{v.line}" if v.line else ""
                lines.append(f"- `{v.file}{loc}` [{v.rule}] ({v.severity}) — {v.message}")
        else:
            lines.append("- ✅ No violations found")

        if c.architectural_notes:
            lines += ["", "**Architectural Notes:**"]
            for note in c.architectural_notes:
                lines.append(f"- {note}")

        lines += ["", f"**Remediation Plan:**\n{c.remediation_plan}"]

    return "\n".join(lines)


def _format_final_report(report: FinalReport) -> str:
    """Render the final report as a markdown string."""
    d = report.decision
    status_emoji = {"APPROVED": "✅", "REJECTED": "❌", "NEEDS_REVISION": "🔄"}.get(
        report.resolution_status, "❓"
    )
    lines = [
        f"## Final Report — {status_emoji} {report.resolution_status}",
        "",
        f"**Maintainer Decision:** {'Approved' if d.approved else 'Not approved'}",
    ]
    if d.assignee:
        lines.append(f"**Assigned to:** @{d.assignee}")
    if d.comments:
        lines.append(f"**Comments:** {d.comments}")
    lines += ["", "---", _format_review_packet(report.triage)]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Workflow Graph Definition
# ---------------------------------------------------------------------------

root_agent = Workflow(
    name="open_source_diplomat",
    description=(
        "Automated mediator and technical compliance reviewer for open-source "
        "repositories. Classifies incoming PR/issue/diff content, routes to a "
        "Moderator or Architectural Reviewer, then gates on Human-in-the-Loop "
        "approval from a core project maintainer."
    ),
    edges=[
        # ── Ingestion & classification ──────────────────────────────────────
        # START feeds the raw text into the classifier LLM agent
        ("START", classifier_agent),
        # classify_input reads the LLM output from state and routes
        (classifier_agent, classify_input),

        # ── Branch: heated disagreement ─────────────────────────────────────
        (classify_input, moderator_agent, ContentKind.DISAGREEMENT.value),
        (moderator_agent, prepare_moderation_triage),

        # ── Branch: compliance evaluation ───────────────────────────────────
        (classify_input, architectural_reviewer, ContentKind.COMPLIANCE.value),
        (architectural_reviewer, prepare_compliance_triage),

        # ── Unknown content: direct triage pass-through ──────────────────────
        # For unknown content, classify_input already built the TriagePackage
        # and stored it in state — skip straight to HITL
        (classify_input, human_triage, ContentKind.UNKNOWN.value),

        # ── Merge paths into HITL triage node ───────────────────────────────
        (prepare_moderation_triage, human_triage),
        (prepare_compliance_triage, human_triage),

        # ── Post-approval: finalize and emit report ──────────────────────────
        (human_triage, finalize_report),
    ],
)
