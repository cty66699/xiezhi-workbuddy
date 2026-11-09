# Sanitization report

- Generated: 2026-11-09T10:21:31Z
- Policy: clean-room whitelist; runtime data, install manifests, local reports, caches, archives and binary avatars excluded
- Files included: 56
- Sensitive pattern findings: 0
- Result: PASSED

## Excluded by design

- `runs/`: real run ledgers, tickets, receipts, agent IDs, artifacts and reports
- `dist/`: local deployment archives
- `DEPLOYMENT_REPORT.html`: machine-specific deployment report
- `__pycache__/` and compiled bytecode
- `models.json`, WorkBuddy internal product caches, install manifests
- Generated binary avatars and raw image-generation outputs

## Replacement summary

- Total sanitized occurrences: 8
- Categories: local paths, account identifiers, emails, real names, API-key-like strings, provider endpoints, session/agent IDs, real run IDs, placeholder author fields and generated avatar filenames.
- Raw detection patterns are intentionally not reproduced in this public report.

## Forbidden-pattern scan

- Files scanned: all text files in the release except the scanner itself.
- Findings: 0
- Result: PASSED

Run `python scripts/verify_redaction.py` after cloning to repeat the check.
