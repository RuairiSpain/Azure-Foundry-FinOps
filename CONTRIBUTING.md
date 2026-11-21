# Contributing

Thanks for helping. This repository holds two projects that ship together:

| Folder | What it is |
|---|---|
| `foundry-finops/` | The Python SDK (`foundry_finops`), the `foundry-finops` command line, the example dashboard and the docs |
| `foundry-finops-cowork/` | The MCP connector (Entra sign-in with on-behalf-of) and the Copilot Cowork plugin package |

The connector imports the SDK, so a change to the SDK can break the connector. Run both test suites before you push.

## Set up

You need Python 3.10 or newer. Python 3.10 is the oldest version CI tests, so avoid syntax or library calls newer than
that.

```bash
git clone https://github.com/RuairiSpain/Azure-Foundry-FinOps.git
cd Azure-Foundry-FinOps
python -m venv .venv && source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -e "foundry-finops[dev,pandas]"              # SDK, pytest, ruff, mypy, type stubs
pip install PyJWT cryptography                           # extra dependencies of the connector tests
```

Everything runs without Azure. The tests use a fake HTTP session, and `foundry-finops --demo ...` and the dashboard use
built-in illustrative data.

## Check your change

These are the same checks CI runs on every pull request (`.github/workflows/ci.yml`):

```bash
# SDK, from foundry-finops/
cd foundry-finops
ruff check src tests examples
mypy
pytest

# Connector, from the repository root
cd ..
ruff check --config foundry-finops/pyproject.toml foundry-finops-cowork
PYTHONPATH=foundry-finops-cowork python -m pytest -q foundry-finops-cowork/tests
```

`mypy` runs in strict mode and must be clean. If you change a workflow file, lint it with
[actionlint](https://github.com/rhysd/actionlint).

## Pull requests

`main` is protected (see `.github/rulesets/`): no direct pushes, and the CI checks must pass.

1. Branch from `main` and keep the change focused. One concern per pull request is easier to review.
2. Add or update tests. A bug fix should come with a test that fails without the fix.
3. Add a line to `foundry-finops/CHANGELOG.md` under **Unreleased** for anything a user would notice.
4. Update the docs when behaviour changes (`foundry-finops/README.md`, `foundry-finops/docs/SETUP.md`, and
   `foundry-finops-cowork/docs/SETUP.md`).
5. Open the pull request, describe what changed and why, and say how you tested it. If you couldn't test against real
   Azure, say so.

Commit messages: a short summary line in the imperative ("Fix LRO polling on 204"), then a paragraph on the why when it
isn't obvious.

## Code conventions

* Type hints everywhere. `from __future__ import annotations` at the top of each module.
* `ruff` decides formatting and import order. Run `ruff check --fix` to apply the automatic fixes.
* Raise the SDK's own exceptions (`foundry_finops.errors`) for service failures and `ValueError` for bad arguments.
  Don't let a raw `requests` or `azure` exception escape the transport.
* Prefer small, explicit functions over clever ones. Say what you mean in comments and docstrings, and explain the why
  rather than repeating the code.

### Rules that protect users

* **Never send credentials to a URL you built from input.** All Azure Resource Manager calls go through
  `_Operations._url` and `_arm`, which only allow the cloud's own ARM host. Encode names you put into a path with
  `self._seg(name)` and escape OData values with `odata_quote`.
* **Don't log or return secrets**, tokens, API keys, or tool arguments. The MCP server logs only the tool name, user
  object ID, outcome and duration.
* **Write operations must respect read-only mode** (`self._guard()` in `LiveService`/`DemoService`) and need an
  honest `destructiveHint` on the MCP tool.
* **Don't invent numbers.** If a value can't be obtained, return `None` or a note that says why, as the cost-control
  usage estimate does. Never fall back silently to demo data.
* **Never commit real subscription IDs, tenant IDs, keys or exported `FinOps/` data.** Billing data is sensitive;
  `FinOps/` is in `.gitignore`.

## Tests

* Put SDK tests in `foundry-finops/tests/` and connector tests in `foundry-finops-cowork/tests/`.
* HTTP is faked with `tests/fakes.py` (`FakeSession`, `make_client`). Routes added later take priority over earlier ones,
  so add a catch-all first and the specific response after it. `times=n` makes a route answer `n` times and then fall
  through to the earlier route.
* Test through the public API where you can, and assert on the request that was sent (URL, parameters, headers, body)
  as well as on the result.

## Common changes

**Add a query to `foundry-finops download`.** Append a `Query(name, description, run)` to `QUERIES` in
`foundry-finops/src/foundry_finops/export.py`. The name becomes the file name, so keep it lower-case with
underscores. Use `needs=("setting",)` if it depends on an optional setting, raise `QuerySkipped` when it can't run for
the request, and use `ctx.note(...)` to record caveats. Add the role it needs to the table in
`foundry-finops/README.md`. `tests/test_export.py` checks that every name is unique and file-safe.

**Add or change an MCP tool.** Edit `foundry-finops-cowork/mcp_server/tools.py`. The `inputSchema` is enforced by the
server, so keep it accurate (types, bounds, `required`, `additionalProperties`). Then regenerate the tool description the
plugin ships with:

```bash
cd foundry-finops-cowork
PYTHONPATH=. MCP_AUTH=none FINOPS_DEMO=1 python -m mcp_server.app &        # starts on 127.0.0.1:8000
python scripts/capture_tools.py --url http://127.0.0.1:8000/mcp --out plugin/appPackage/tools/connector-tools.json
kill %1
```

Commit the changed `connector-tools.json` with the code. If you add or change the plugin's skills or manifest, rebuild
the package with `scripts/configure_plugin.py` as described in `foundry-finops-cowork/docs/SETUP.md`.

**Add a REST operation to the SDK.** Add a method to the right class in `foundry_finops/operations/`, a model in
`models.py` if it returns structured data, and the API version to `ApiVersions` in `_config.py` so users can override it.
Use `self._list` for paged results.

## Releasing

Maintainers only. Set the version in `foundry-finops/src/foundry_finops/_version.py`, move the **Unreleased** notes in
`CHANGELOG.md` under the new version and date, merge, then push a tag such as `v0.2.0`. The `publish` workflow builds the
package and publishes it to PyPI through trusted publishing. It refuses to build while `pyproject.toml` still contains
a `<your-org>` placeholder.

## Security issues

Please don't open a public issue for a vulnerability. Use the repository's **Security > Report a vulnerability** page
(GitHub private vulnerability reporting). Include the affected version, how to reproduce, and what an attacker could do.

## Licence

By contributing you agree that your contribution is licensed under the [MIT License](LICENSE).
