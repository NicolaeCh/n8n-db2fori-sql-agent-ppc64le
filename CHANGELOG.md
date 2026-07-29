# Changelog

## 1.0.1 — 2026-07-29

- Removed all references to unrelated monitoring components.
- Added `gssapi` as an explicit runtime dependency.
- Added a multi-stage ppc64le build for the Python GSSAPI extension.
- Added a build-time `gssapi` and Mapepire import verification.
- Pinned `mapepire-python` to 0.3.0 for reproducible builds.
- Added an importable n8n workflow that selects Db2 for i rows and sends an HTML table with the Brevo node.
- Generalized example schemas and table names for standalone use.
