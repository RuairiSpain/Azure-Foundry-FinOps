# Branch protection for `main`

`main.json` is a GitHub **ruleset** for the default branch. GitHub can't apply it from the repository, so import it
once:

1. Repository **Settings > Rules > Rulesets > New ruleset > Import a ruleset**.
2. Choose `.github/rulesets/main.json`, review it, and **Create**.

It enforces, for everyone including admins (there are no bypass actors):

| Rule | Effect |
|---|---|
| Restrict deletions | `main` can't be deleted |
| Block force pushes | History can't be rewritten |
| Require a pull request | No direct pushes to `main` |
| Dismiss stale approvals, resolve threads | A new push clears earlier approvals; open review threads must be resolved |
| Require status checks | `lint`, `sdk (3.10 to 3.13)` and `mcp-server (3.10, 3.12, 3.13)` from `.github/workflows/ci.yml` must pass, on a branch that is up to date with `main` |

**Approvals are set to 0.** GitHub doesn't let you approve your own pull request, so requiring one approval would stop a
sole maintainer from merging anything. When there is a second reviewer, raise `required_approving_review_count` to 1
and import again, or edit the ruleset in Settings.

**If you rename a CI job or change the Python versions**, update the `context` names in `main.json` to match
(`<job> (<python>)` for matrix jobs), otherwise merges will wait forever for a check that no longer exists.

To let an admin bypass in an emergency, add `{ "actor_id": 5, "actor_type": "RepositoryRole", "bypass_mode": "always" }`
to `bypass_actors` (5 is the admin role).
