## What and why

<!-- What does this change, and what problem does it solve? Link the issue: "Fixes #123". -->

## Type of change

- [ ] Bug fix
- [ ] New feature or query
- [ ] Documentation only
- [ ] CI, tooling or repository settings
- [ ] Breaking change (explain below)

## How I tested it

<!-- Say what you ran. If you couldn't test against real Azure, say so. -->

- [ ] `ruff check` (SDK and connector) passes
- [ ] `mypy` passes
- [ ] SDK tests pass (`pytest` in `foundry-finops/`)
- [ ] Connector tests pass (`foundry-finops-cowork/tests`)
- [ ] I added or updated tests, or this change needs none (documentation only)
- [ ] I tried it against real Azure, or I can't and have said so above

## Checklist

- [ ] `foundry-finops/CHANGELOG.md` has a line under **Unreleased** (for user-visible changes)
- [ ] Docs updated (README, `SETUP.md`, the roles table) if behaviour or required permissions changed
- [ ] If I changed an MCP tool, I regenerated `foundry-finops-cowork/plugin/appPackage/tools/connector-tools.json`
- [ ] If I added a `download` query, its role is in the README table
- [ ] No secrets, subscription or tenant IDs, or exported `FinOps/` billing data in the diff, commits or description

## Notes for reviewers

<!-- Anything risky, anything you're unsure about, or follow-up work. -->
