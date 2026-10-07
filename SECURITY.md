# Security policy

FootnoteOne handles your provider API keys. The contract is simple: keys are read from the environment variables named in `footnote.toml`, sent only in request headers, and never written to disk, stored in a raw response, printed in a report, a help text or an error message. A committed test (`tests/test_no_key_leaks.py`) checks every one of those paths on each change.

## Reporting a vulnerability

Please report privately through GitHub's advisory form: https://github.com/shehral/footnoteone/security/advisories/new. Do not open a public issue for a key-handling or data-safety problem. You will get an acknowledgement within a week; fixes ship as a patch release with a note in `CHANGELOG.md`.

In scope: anything that could expose a key, write outside `.footnote/` and `reports/`, spend beyond the configured budget, or misreport what was measured. Out of scope: the behaviour of the provider APIs themselves.

## Supported versions

Only the latest release on `main` is supported.
