# /review — Review an implementation against its spec

Read the spec and the implementation. Go requirement by requirement.

## Process:

1. Read the spec file (`specs/<name>.md`). If there is no spec, say so.
2. For each numbered requirement, state: PASS / FAIL / PARTIAL.
   - PASS: requirement is verifiably met.
   - FAIL: requirement is not met. Name the exact gap and the specific fix needed.
   - PARTIAL: requirement is partially met. State what is missing.
3. For each FAIL or PARTIAL, hand a specific fix back to /build (quote the exact
   requirement number and describe what must change).
4. At the end, give an overall verdict:
   - **CLEAN** — all requirements pass. Ready to ship.
   - **NEEDS WORK** — list the open items.

## Adversarial review rule:

After your initial pass, challenge your own PASSes:
- Is the test actually checking the right thing, or just confirming the code runs?
- Would this break under the edge cases listed in the spec?
- Could someone else verify this from the observable output alone?

If you downgrade any PASS after challenging it, say why.

## Loop:

If the review is not CLEAN: say "Run /build to address the items above."
If the review is CLEAN: say "Review is clean. Ready to commit and ship."
