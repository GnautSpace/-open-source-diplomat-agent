# open-source-diplomat-agent — Gemini CLI guidance

## Project Overview

ADK 2.0 graph workflow agent that acts as an automated mediator and technical
compliance reviewer for open-source repositories.

## Project Structure

```
open-source-diplomat-agent/
├── app/
│   ├── __init__.py       # Re-exports root_agent
│   ├── agent.py          # Workflow graph (START → classify → branch → HITL → report)
│   ├── schemas.py        # Pydantic models for all node I/O
│   ├── prompts.py        # LLM system instructions
│   └── tools.py          # Code-execution tools (ruff lint, shell, syntax check)
├── tests/
│   └── test_agent.py     # Schema + structure smoke tests
├── pyproject.toml
├── .env.example
├── GEMINI.md             # This file
└── README.md
```

## Key Rules (read before touching any file)

1. **Never change `root_agent.name`** — it must stay `"open_source_diplomat"`.
2. **Never change the model** in `agent.py` unless explicitly asked.
3. **Never skip HITL** — the `human_triage` node must always be reachable from
   both the moderation and compliance paths.
4. **Always use `output_schema`** on every `LlmAgent` — raw `types.Content`
   outputs break downstream function nodes.
5. **Preserve all `output_key` values** — they feed `ctx.state` lookups in
   function nodes.

## Running Locally

```bash
# Install dependencies (requires Python 3.11+)
uv sync

# Copy and fill in secrets
cp .env.example .env

# Interactive playground
adk web app

# One-shot smoke test
adk run app "Please review this PR: [paste diff here]"
```

## Workflow Graph

```
START
  │
  ▼
classifier_agent (LlmAgent)
  │
classify_input (function — routes by ContentKind)
  ├─(disagreement)──► moderator_agent (LlmAgent)
  │                       │
  │               prepare_moderation_triage
  │                       │
  ├─(compliance)───► architectural_reviewer (LlmAgent + tools)
  │                       │
  │               prepare_compliance_triage
  │                       │
  └─(unknown)──────────────┘
                           │
                     human_triage  ← [REQUIRES_MANUAL_REVIEW] HITL interrupt
                           │
                    finalize_report
```

## Extending the Agent

- **Add a new route**: Add a new `ContentKind` enum value in `schemas.py`,
  create the LLM agent in `agent.py`, and add the edge tuple.
- **Add a tool**: Add a function to `tools.py` and append it to `REVIEWER_TOOLS`.
- **Update prompts**: Edit the relevant constant in `prompts.py` — no graph
  changes needed.
- **Add eval cases**: Place JSONL files in `evals/` and run `adk eval`.
