# /spec — Write a specification before building

Interview the user one question at a time (not all at once) to understand
what they want to build. Then write a spec file at `specs/<name>.md`.

## The spec file must contain:

1. **Objective** — one sentence: what problem does this solve?
2. **Requirements** — exact, numbered, verifiable requirements. Each one must be
   checkable by someone other than the author (a test, a measurement, an observable state).
3. **Edge cases** — at least 2-3 failure modes or boundary conditions.
4. **Definition of done** — a concrete checklist. Someone else could verify it
   without knowing the implementation.

## Interview process:

Ask only ONE question per message. Wait for an answer before asking the next.
Typical questions (adapt to the task):
- What is the one-sentence goal of this?
- What existing system does it connect to?
- What does success look like — how will you know it's working?
- What are the most likely failure modes?
- Is there anything that must NOT change (interfaces, file formats, config schema)?

When you have enough to write a clear spec, stop interviewing and write the file.
Confirm the spec with the user before proceeding.

## Output

Write `specs/<kebab-case-name>.md` and tell the user the filename.
Do not start building until the user says to proceed.
