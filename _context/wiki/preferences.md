# Working Preferences

## Coding Style

- Follow existing code style in the project — don't introduce new patterns without a reason
- Make **targeted, minimal changes** — only touch what is needed to solve the problem
- Don't over-engineer — prefer the simplest solution that works
- No unnecessary abstractions, error handling for impossible cases, or speculative features

## Communication Style

- **Be concise** — minimal explanation unless asked
- Just do the work; don't narrate every step
- **Check in at key decision points** rather than assuming — especially for architecture choices, naming, or anything with multiple valid approaches
- Surface trade-offs briefly when they matter; skip them when they don't

## How to Work with AI (Bob)

- Read relevant files before answering questions about the codebase — no speculation
- Produce the minimal change that solves the problem
- Run validation (lint, tests, type checks) before reporting a task complete
- Use the wiki: check `_context/wiki/index.md` at the start of a task to decide if context is needed
- Offer to update the wiki after tasks that yield durable knowledge, but **wait for approval** before writing

## Project-Specific Standards

<!-- Add project conventions here as they are established -->
- Language: **Python**; web framework: **Streamlit**
- Input data format for on-demand metrics: Excel export (see project.md)
- Output: on-screen **and** downloadable **Excel** file
