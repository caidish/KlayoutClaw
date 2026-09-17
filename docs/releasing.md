# CI, release, and PyPI handoff

KlayoutClaw uses three required pull-request checks and a two-stage release
pipeline. A merge to `main` prepares a semantic version and annotated tag; a
matching tag builds the distributions, publishes them to PyPI, and creates the
GitHub release.

## Required merge checks

Create a ruleset for `main` that requires a pull request and these exact checks:

- `Lint`
- `Security`
- `PyTests`

The Ruff gate covers canonical install, package, release, and packaging-test
sources. Generated payload copies are verified by synchronization and artifact
tests instead of being linted twice; broader legacy tests retain their existing
style until that debt is addressed separately.

GitHub's dependency-review API rejects fork repositories. On forks, the
`Security` job records that limitation and still enforces pip-audit, Bandit,
and zizmor. On the upstream non-fork repository, dependency review also runs
and fails the gate for newly introduced vulnerabilities of moderate severity
or higher.

Also require the branch to be current, block force pushes and deletion, and
require all conversations to be resolved. Workflows alone report checks; the
repository ruleset is what makes them merge gates.

For the one-time bootstrap, merge the pull request that introduces
`pr-gate.yml` only after its recorded local validation and normal review.
GitHub does not run a newly introduced `pull_request` workflow until that
workflow exists on the default branch. Configure the required-check ruleset
after this bootstrap merge so it cannot wait forever for checks that do not yet
exist; every subsequent pull request will report the three checks above.

## Release GitHub App

The default `GITHUB_TOKEN` cannot trigger the tag workflow from a tag it
creates. Install a dedicated GitHub App on this repository with **Contents:
read and write** and store:

- GitHub Actions variable `RELEASE_APP_CLIENT_ID`
- GitHub Actions secret `RELEASE_APP_PRIVATE_KEY`

Add the App to the `main` ruleset bypass list so it can commit the generated
version and changelog after an already-gated merge. No human user or long-lived
personal access token is used by the workflow.

## PyPI trusted publisher

Create a protected GitHub environment named `pypi`, ideally with a required
reviewer. Configure a pending PyPI Trusted Publisher with:

- Project: `klayoutclaw`
- Repository owner: the current GitHub repository owner
- Repository: the current repository name
- Workflow: `release.yml`
- Environment: `pypi`

The release job requests a short-lived OIDC credential and runs `uv publish`;
there is no stored PyPI token.

## Release sequence

1. A pull request passes `Lint`, `Security`, and `PyTests` and is merged.
2. `release-prepare.yml` calculates the next version with git-cliff. The first
   release bootstraps the version already declared in `pyproject.toml`.
3. The workflow synchronizes package/plugin/server versions,
   regenerates the packaged payload and `CHANGELOG.md`, commits the result, and
   pushes an annotated `vMAJOR.MINOR.PATCH` tag with the GitHub App token.
4. `release.yml` validates the tag, tests and builds the artifacts, publishes
   to PyPI through Trusted Publishing, and creates or updates the GitHub release.

## Upstream handoff

Before removing the fork publisher, add the upstream maintainer as a PyPI
Owner, configure a trusted publisher for the upstream repository, and complete
one upstream release. Then remove the fork publisher and finally remove the
fork owner's PyPI role. Trusted publishers are project configuration and are
not removed automatically when a user is removed.
