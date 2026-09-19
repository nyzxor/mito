# prompts/

Agent-writable (via the evolution gate only) prompt fragments. Ordering is fixed for caching:
`constitution.md → hard_rules.md → tool_policy.md → response_contract.md` form the stable prefix;
the skills catalog and memory index are appended by the runtime. L0 total ≤ 3,000 tokens
(measured in CI). The constitution encodes the Prime Directives as pedagogy — enforcement lives
in `handbrake/` and `policy/`.
