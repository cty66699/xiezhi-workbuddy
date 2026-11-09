# Contributing

1. Keep all examples free of personal paths, account IDs, API keys, and real run artifacts.
2. Add tests for changes to the ledger, receipts, stage advancement, or report generation.
3. Run `python -m unittest discover -s tests -p "test_*.py"`.
4. Run `python scripts/verify_redaction.py`.
5. Do not add external model API calls or custom model endpoint support to the default configuration.

Pull requests should include a short description of the workflow change and the verification commands used.
