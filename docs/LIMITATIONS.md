# Limitations

- WorkBuddy expert packages do not provide a reliable per-member model-binding field. This project uses an Agent-type expert entry plus named subagents instead of a Team-type expert group.
- Custom subagent definitions may require a WorkBuddy restart before they are discoverable.
- Host dispatch evidence consists of an explicit `model` parameter, agent/task identifiers, tickets, raw output, and hashes. It is not a vendor-side cryptographic attestation.
- Exact credit consumption is controlled by WorkBuddy. If the client does not expose it, record `unavailable`; do not estimate from tokens.
- Model IDs and availability can change by client version, account, and subscription.
- Agent Teams are experimental in some hosts and are not required by the default workflow.
- The project does not support external model APIs, custom endpoints, API keys, Token Plan, or `custom-local:*` models.
