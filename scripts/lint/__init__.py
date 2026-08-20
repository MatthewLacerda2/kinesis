"""Conventions linter: the rules in CLAUDE.md that a machine can check.

These rules share one failure mode -- they break silently and stay broken. The
app works fine with tracking/ importing from ui/, with a file 40 lines over the
cap, and with an MCP argument nobody described; each only bites later, when the
tracker needs to restart alone, or when an agent calls a tool wrongly in a
session with no human watching. A convention nobody re-reads decays at exactly
the rate nobody re-reads it, so these three are checks instead of prose.

One module per rule, each with a `main()` and each runnable alone while
iterating on it:  python -m scripts.lint.architecture
`python -m scripts.lint` runs all three, which is what ./run.sh check calls.
"""
