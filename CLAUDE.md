# Watch Party Manager Development Guidelines

## Project Goals

- Follow the Software Requirements Specification (SRS).
- Implement one functional requirement at a time.
- Keep business logic separate from Discord-specific code.
- Prefer maintainable, readable code over clever code.
- Do not implement future requirements unless requested.
- When requirements are ambiguous, ask for clarification rather than making assumptions.
- Prefer the simplest solution that satisfies the current SRS.

## Coding Standards

- Python 3.12
- Use dataclasses for domain models unless another approach is justified.
- Write or update unit tests for every implemented functional requirement.
- Keep files focused on a single responsibility.
- Prefer small, well-named classes and functions.
- Do not create placeholder files or empty classes. Every new file should exist to implement a specific functional requirement.
- Prefer explicit, descriptive names over abbreviated names for files, classes, functions, and variables.
- Prefer self-documenting code. Avoid comments that simply restate what the code is doing. Use comments to explain intent or non-obvious decisions.

## Workflow

1. Review the relevant SRS section.
2. Implement only the requested functional requirement.
3. Run unit tests.
4. Summarize:
   - Files changed
   - Design decisions
   - Test results
5. Do not commit or push unless explicitly instructed.
6. Minimize permission prompts by batching related file operations before running tests. If a destructive operation requires repeated approval, stop and ask the user to perform it manually instead of retrying.
7. Keep commits focused. Each commit should implement one functional requirement or one logical unit of work.

## AI Collaboration

ChatGPT is acting as the project's software architect and code reviewer.

If architectural guidance from ChatGPT does not conflict with the SRS, treat it as the project's coding standard.

If unsure, ask rather than guessing.

If an operation fails repeatedly, do not retry indefinitely. Explain the problem, propose a solution, and wait for user confirmation before trying a different approach.

## Definition of Done

A functional requirement is not complete until:

1. The requested requirement is implemented.
2. Relevant unit tests are added or updated.
3. Tests have been run successfully.
4. The implementation has been reviewed against the SRS.
5. Any relevant documentation or checklist updates are complete.

When summarizing completion, include the exact test command that was run.
