# Changelog

## 2026-10-13 20:03 (Asia/Taipei) — Initial public source snapshot

- Published the Error Band Studio Python application, packaging scripts, documentation, and tests.
- Files: `.gitignore`, `README.md`, `README_PORTABLE.md`, `CHANGELOG.md`, `main.py`, `skin_analysis/`, `tests/`, build and launcher scripts, requirements files, and the parity fixture.
- Verification: reviewed the upload file list; excluded experimental spreadsheets, local build outputs, virtual environments, bytecode, and workstation cleanup records; replaced the workstation-specific default data path in this public snapshot. Automated tests were not run during publication.

## 2026-10-13 22:48 +08:00 (Asia/Taipei)

- Summary: Synchronized the latest Error Band Studio Python implementation into its standalone repository, including scientific-integrity fixes, portable-source checks, and a repository-specific scientific behavior contract. Updated the README for the standalone tool and fixed the flattened repository path to the shared parity fixture.
- Files: `README.md`, `README_PORTABLE.md`, `SCIENTIFIC_BEHAVIOR_CONTRACT.md`, `skin_analysis/`, `tests/`, `requirements-tests.txt`, `CHANGELOG.md`.
- Verification: Source synced from Cap-s-analysis commit `5ac344a`; path references were adjusted for the standalone repository layout. Tests were not run during this separation.
