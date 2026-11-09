# Security Policy

## Reporting vulnerabilities

Please use GitHub Security Advisories or open a GitHub Issue without including secrets, account identifiers, API keys, or private configuration.

## Scope

This project is a local orchestration toolkit. It does not host a service, does not require model API keys, and does not transmit prompts to third-party model endpoints from its own scripts. Model calls are expected to occur inside the WorkBuddy native runtime.

## Do not include

- API keys, tokens, or credentials
- `models.json` or custom model configuration
- WorkBuddy internal product configuration or cache files
- Real run artifacts containing absolute paths or agent/session identifiers
- Personal directories, emails, or account IDs

Run `python scripts/verify_redaction.py` before publishing changes.
