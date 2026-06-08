# CodeWiki Copilot Operating Rules

These instructions apply to all work in this repository.

## Primary Goals

- Preserve behavior unless the task explicitly requests a change.
- Prefer minimal diffs over wide refactors.
- Keep changes grounded in the product docs and implementation plan.

## Source Of Truth

- Product requirements: docs/PRD.md
- Implementation sequencing: docs/IMPLEMENTATION_PLAN.md
- Project entry points: codewiki/cli.py and codewiki/pipeline.py

## Coding Rules

- Use Python 3.11 compatible code.
- Keep functions focused and avoid hidden side effects.
- Add concise comments only when logic is not obvious.
- Avoid introducing new dependencies unless justified.

## Safety And Validation

- Before editing, identify impacted modules and likely regressions.
- After editing, run targeted validation commands when feasible.
- Do not change unrelated files.

## Working Style

- First show a short plan, then execute.
- Report changed files and why each was changed.
- Call out assumptions and remaining risks.
