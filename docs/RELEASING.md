# Release status and verification

## 1.1.7 — 19 October 2026

The GitHub release provides a wheel and source archive built by CI. Validation
includes 41 regression tests on Linux (Python 3.10, 3.11, 3.12) and Windows
(Python 3.12), wheel-content checks, and an installed-wheel smoke test covering
index, status, context, the HTTP viewer, MCP initialization and a tool call.

After the PyPI Trusted Publisher was configured, the
[Publish PyPI workflow](https://github.com/lorismascio17/droste-memory/actions/runs/39250479711)
passed all checks and published the 1.1.7 wheel and source archive to
[PyPI](https://pypi.org/project/droste-memory/1.1.7/). The
[Publish MCP Registry workflow](https://github.com/lorismascio17/droste-memory/actions/runs/39250932125)
then published [version 1.1.7](https://registry.modelcontextprotocol.io/v0.1/servers/io.github.lorismascio17%2Fdroste-memory/versions/1.1.7).

## PyPI publication configuration

The `Publish PyPI` workflow uses short-lived OIDC credentials, not a stored API
token. The PyPI project has this Trusted Publisher identity:

- Owner: `lorismascio17`
- Repository: `droste-memory`
- Workflow filename: `publish-pypi.yml`
- Environment: `pypi`

For future releases, dispatch `Publish PyPI` at the intended release ref. It
runs the complete CI workflow and publishes only the verified distributions.
Then run `Publish MCP Registry`; its alignment guard prevents announcing a
version that is not available on PyPI. See
[PyPI's official instructions](https://docs.pypi.org/trusted-publishers/adding-a-publisher/)
if the repository or workflow identity changes.

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
