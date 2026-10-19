# Release status and verification

## 1.1.7 — 19 October 2026

The GitHub release provides a wheel and source archive built by CI. Validation
includes 41 regression tests on Linux (Python 3.10, 3.11, 3.12) and Windows
(Python 3.12), wheel-content checks, and an installed-wheel smoke test covering
index, status, context, the HTTP viewer, MCP initialization and a tool call.

PyPI publication was attempted after those checks and rejected with
`invalid-publisher`: no Trusted Publisher matched the workflow identity.
PyPI and the MCP Registry therefore remain at 1.1.6. The README installs the
fixed GitHub wheel directly; this should not be confused with a PyPI release.

## PyPI publication configuration

The `Publish PyPI` workflow uses short-lived OIDC credentials, not a stored API
token. The PyPI project owner can register the following identity on the
project's Publishing page:

- Owner: `lorismascio17`
- Repository: `droste-memory`
- Workflow filename: `publish-pypi.yml`
- Environment: `pypi`

See [PyPI's official instructions](https://docs.pypi.org/trusted-publishers/adding-a-publisher/).
After configuration, dispatch `Publish PyPI` at the intended release ref. It
runs the complete CI workflow and publishes only the verified distributions.
Then run `Publish MCP Registry`; its alignment guard prevents announcing a
version that is not available on PyPI.

## Ongoing checks

`Growth Radar` records dated public GitHub/PyPI/MCP observations and download
counts in a 90-day Actions artifact. Download counts measure distribution,
not active use. No user-code telemetry was added.

GitHub automatically disables scheduled workflows after 60 days without
repository activity. Both existing schedules were re-enabled during this
maintenance pass; they still have that platform limit. See
[GitHub's documentation](https://docs.github.com/en/actions/managing-workflow-runs-and-deployments/managing-workflow-runs/disabling-and-enabling-a-workflow).

The current persistence format isolates databases sharing a directory. Old
shards are preserved during migration. Back up the metadata file and its
`.droste` directory together; use a single writer per database. Old cache files
can remain on disk but are no longer used by the new format.
