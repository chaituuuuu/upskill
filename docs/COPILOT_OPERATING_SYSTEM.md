# Copilot Operating System For This Repository

This guide explains how to use Copilot agents, instructions, prompts, and hooks effectively in this repo.

## What Is Wired Right Now

- Global repo instructions: .github/copilot-instructions.md
- Custom agents:
  - .github/agents/implementor.agent.md
  - .github/agents/prd-reviewer.agent.md
- File-scoped instruction:
  - .github/instructions/python-codewiki.instructions.md
- Reusable prompts:
  - .github/prompts/implement-task.prompt.md
  - .github/prompts/review-prd.prompt.md

## Why Your Earlier Approach Felt Fragile

If you only start two separate chats and paste "read this as agents.md", the model may follow it for that thread but it is not guaranteed to be auto-discovered later.

Putting files in .github with correct frontmatter makes them discoverable and reusable by Copilot features directly.

## How To Use Daily

1. Open Copilot Chat.
2. In agent picker, choose Implementor for coding tasks.
3. For requirement/design checks, choose PRD Reviewer.
4. Type / and run:
   - Implement Task
   - Review PRD Or Plan

You can still write plain prompts, but these templates keep quality consistent.

## Sonnet-Only Mode

If you prefer Sonnet only, set Sonnet in the model picker before running tasks.

Notes:
- Agent frontmatter can suggest fallback models.
- Your active model selection in chat is still important.
- Strong instructions and prompt format matter more than model switching.

## Instructions Vs Agents Vs Prompts Vs Hooks

- Instructions: always-on or file-scoped guidance.
- Agents: specialized role, tools, and behavior.
- Prompts: reusable task templates.
- Hooks: deterministic automation at lifecycle events.

## Hooks In Practice

Hooks are optional. Use them only for behavior that must be enforced, for example:

- blocking dangerous terminal commands
- running lint after edits

Recommended path:

1. Start with instructions + agents + prompts.
2. Add hooks after your workflow is stable.

## Next Upgrade Ideas

1. Add language-specific instructions for docs and markdown formatting.
2. Add a guarded hook that asks approval before destructive commands.
3. Add a release-review prompt that checks docs, changelog, and migration impact.
