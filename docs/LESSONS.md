# Design lessons

1. Use actual model IDs from the client catalog, not display names from documentation.
2. Hard-isolate built-in credit models from custom API models and `custom-local:*` identifiers.
3. A Skill cannot change the underlying model; use named subagents with an explicit `modelId`.
4. Custom subagent files may require a host restart before discovery.
5. Team-type expert packages do not guarantee per-member model binding; prefer an Agent-type entry plus named subagents.
6. Define evidence levels before review: host dispatch evidence is acceptable, vendor-side telemetry is not claimed.
7. Rate limits must stop the route or be explicitly recorded; never silently substitute a model.
8. Preserve historical source files. Changing a source after a run should correctly fail source-integrity verification.
9. Keep runtime data out of public releases.
10. Use `standard` as the default mode and escalate only when needed.
