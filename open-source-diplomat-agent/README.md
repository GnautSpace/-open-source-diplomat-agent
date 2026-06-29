# open-source-diplomat-agent

An **ADK 2.0 graph workflow agent** that serves as an automated mediator and
technical compliance reviewer for open-source repositories.

---

## What it does

The agent ingests raw text streams from repository activity — PR discussion
comments, GitHub issue threads, or unified code diffs — and routes them
through a structured review pipeline:

```
                        ┌─────────────────────────────┐
   PR / Issue / Diff    │                             │
 ──────────────────────►│      Classifier Agent       │
                        │  (disagreement | compliance)│
                        └──────────────┬──────────────┘
                                       │
              ┌────────────────────────┴────────────────────────┐
              │ disagreement                          compliance │
              ▼                                                  ▼
  ┌───────────────────────┐                    ┌────────────────────────────┐
  │   Moderator Agent     │                    │  Architectural Reviewer    │
  │                       │                    │  (+ code_execution tools)  │
  │ • Parse viewpoints    │                    │                            │
  │ • Name core conflict  │                    │ • Run ruff / lint          │
  │ • Draft compromise    │                    │ • Check baseline           │
  │ • Suggest next steps  │                    │ • Architectural notes      │
  └──────────┬────────────┘                    └──────────────┬─────────────┘
             │                                                │
             └──────────────────┬──────────────────────────-─┘
                                │
                                ▼
                  ┌─────────────────────────────┐
                  │    Human-in-the-Loop Triage  │
                  │                             │
                  │  Tags output with           │
                  │  [REQUIRES_MANUAL_REVIEW]   │
                  │                             │
                  │  ⏸️  HALTS until a core      │
                  │  maintainer explicitly       │
                  │  approves the direction      │
                  └──────────────┬──────────────┘
                                 │ (on approval)
                                 ▼
                  ┌─────────────────────────────┐
                  │       Final Report           │
                  │  APPROVED / REJECTED /       │
                  │  NEEDS_REVISION              │
                  └─────────────────────────────┘
```

---

## Nodes

| Node | Type | Description |
|------|------|-------------|
| `classifier_agent` | `LlmAgent` | Classifies input as `disagreement` or `compliance` |
| `classify_input` | Function | Reads classification from state, routes via `Event(route=...)` |
| `moderator_agent` | `LlmAgent` | Parses viewpoints → neutral JSON matrix → compromise proposal |
| `architectural_reviewer` | `LlmAgent` + tools | Lints code, checks baselines, writes remediation plan |
| `prepare_moderation_triage` | Function | Packages moderation output into `TriagePackage` |
| `prepare_compliance_triage` | Function | Packages compliance output into `TriagePackage` |
| `human_triage` | Async generator | HITL: emits `RequestInput`, halts, resumes on maintainer response |
| `finalize_report` | Function | Assembles `FinalReport`, emits markdown to web UI |

---

## Quick Start

### Requirements

- Python 3.11+
- `uv` package manager (`pip install uv` or see [docs](https://docs.astral.sh/uv/))
- A Gemini API key

### Install

```bash
cd open-source-diplomat-agent
uv sync
```

### Configure

```bash
cp .env.example .env
# Edit .env and set GEMINI_API_KEY
```

### Run (interactive web UI)

```bash
adk web app
```

### Run (one-shot CLI)

```bash
adk run app "Here is the PR discussion: [paste your text]"
```

---

## Project Layout

```
open-source-diplomat-agent/
├── app/
│   ├── __init__.py       # Package entry point
│   ├── agent.py          # Workflow graph (root_agent)
│   ├── schemas.py        # Pydantic models for all I/O
│   ├── prompts.py        # LLM system instructions
│   └── tools.py          # Code-execution tools
├── tests/
│   └── test_agent.py     # Smoke tests (schema + structure)
├── pyproject.toml
├── .env.example
├── GEMINI.md
└── README.md
```

---

## Human-in-the-Loop Details

The `human_triage` node uses ADK's `RequestInput` mechanism:

1. **First execution**: The node formats the analysis into a review packet,
   emits it to the UI, and yields a `RequestInput(interrupt_id="maintainer_approval")`.
   The workflow suspends.

2. **Maintainer response**: The maintainer replies with a JSON object:
   ```json
   { "approved": true, "comments": "Looks good, proceeding.", "assignee": "dev-handle" }
   ```
   Plain-text `"yes"` / `"approve"` also accepted.

3. **Resume**: The workflow resumes, parses the decision, and routes to
   `finalize_report` which emits the final status.

---

## Extending

- **New content type**: Add a `ContentKind` value in `schemas.py`, a new
  `LlmAgent` in `agent.py`, and the corresponding edges.
- **New linting tool**: Add a function to `tools.py` and append to `REVIEWER_TOOLS`.
- **Update prompts**: Edit constants in `prompts.py`.

---

## License

Apache 2.0
