# /build — Build exactly what the spec says

Read the spec at `specs/<name>.md` before writing any code.
Build exactly what the spec describes. No extra features, no unrelated refactors.
No "while I'm in here" changes.

## Process:

1. Read the spec file first. If there is no spec, say so and offer to run /spec.
2. List which requirements you plan to cover before writing any code.
3. Build the implementation.
4. After building, list which requirements you covered and which (if any) you did not.
5. If any requirement was not met, say why and what would be needed.

## Constraints:

- Do not change interfaces (topic names, function signatures, config schema,
  file formats) without an explicit requirement in the spec that says to.
- Do not add dependencies without listing them and why they are needed.
- Commit with a message referencing the spec: `feat: <name> — implements specs/<name>.md`

## After building:

Tell the user: "Run /review to check requirements against the implementation."
