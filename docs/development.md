# Development and releases

This guide is for contributors and maintainers with access to the DPT
repository. End-user installation and usage are in [the README](../README.md).

## Development setup

Use Python 3.11 or newer, Git 2.31 or newer, and Node.js 22. Node.js is a build
dependency only; installed release packages include the browser interface.

From the repository root, create a Python environment:

```sh
python -m venv .venv
```

Activate it on macOS or Linux:

```sh
. .venv/bin/activate
```

Or in Windows PowerShell:

```powershell
.\.venv\Scripts\Activate.ps1
```

On Windows, use `py` in place of `python` when creating the environment if needed.

Install the app and development tools, then build the frontend:

```sh
python -m pip install -e . build twine pytest ruff mypy
cd frontend
npm ci
npm run build
cd ..
stateforge-dpt run
```

Development runs use the same per-user data directory as installed releases.
Pass `--data-dir` to keep test or development data separate. See
[configuration](configuration.md) for precedence and path resolution.

## Checks

Run the Python suite and static checks from the repository root:

```sh
python -m pytest -q
python -m ruff check src tests tools
python -m mypy src
```

Rebuild the frontend after interface changes:

```sh
cd frontend
npm ci
npm run build
cd ..
```

## Build distributions

Build the frontend before producing Python packages:

```sh
python -m build
python tools/check_distributions.py dist
python -m twine check dist/*
```

In PowerShell, pass the generated wheel and source archive paths explicitly if
the shell does not expand the wildcard for the Python command.

Both the universal wheel and source distribution include the prebuilt frontend.
Users do not need Node.js to install either one. The archive validator rejects
unexpected workspace content and checks for the included frontend. Do not
publish local databases, credentials, IDE files, or working-copy metadata.

To exercise the installed package, install a freshly built wheel in a clean
environment, then run `python tools/smoke_test_package.py` from the checkout.
The script starts the installed console command from an unrelated working
directory and verifies the frontend, assets, API, and home-directory workspace.
It must not run against an editable install.

The package workflow in `.github/workflows/package.yml` builds once and tests
the wheel on Linux, macOS, and Windows with Python 3.11, 3.12, 3.13, and 3.14.
Each configuration runs the test suite, dependency checks, and installed-package
smoke test.

## Publish through GitHub Actions

Publishing is manual. Ordinary pushes and pull requests do not upload packages
to PyPI. The publishing workflow waits for the complete package workflow before
uploading its verified artifacts.

### One-time Trusted Publishing setup

For a new PyPI project, open
[account publishing settings](https://pypi.org/manage/account/publishing/) and
register a pending GitHub publisher with these fields:

| Field | Value |
| --- | --- |
| PyPI project name | `stateforge-dpt` |
| GitHub owner | `stateforge-org` |
| GitHub repository | `stateforge-dpt` |
| Workflow filename | `publish.yml` |
| Environment | `pypi` |

For an existing project, manage its publisher from the project's **Publishing**
settings instead. Create the matching `pypi` environment in the repository's
**Settings → Environments**. Enable required-reviewer approval where supported
by the repository's visibility and GitHub plan, and restrict release access to
trusted maintainers.

For testing, configure a separate publisher on
[TestPyPI](https://test.pypi.org/manage/account/publishing/) using the same fields
but environment `testpypi`. TestPyPI requires its own account and publisher.

The workflow uses GitHub OIDC with `id-token: write`. No permanent PyPI API token
or GitHub secret is needed. See
[PyPI's Trusted Publishing guide](https://docs.pypi.org/trusted-publishers/) and
[GitHub environment protection](https://docs.github.com/en/actions/reference/workflows-and-actions/deployments-and-environments).

### Release steps

1. Update the version in `pyproject.toml` and review the user-facing README.
2. Push the reviewed changes. `publish.yml` must exist on the default branch
   for GitHub's manual launch button to be available.
3. Open **Actions → Publish pip package (manual) → Run workflow**.
4. Select the release branch and target `testpypi` for a test publication.
5. After verification, run the workflow with target `pypi` for production.

The target defaults to `testpypi`; selecting it does not publish to production.
Changing GitHub's README alone does not change an existing PyPI description.
PyPI stores metadata with the first upload for a version and does not allow
previously uploaded distribution filenames to be reused, even after deletion.

Use a new patch version for application fixes. A documentation- or metadata-only
correction with unchanged application code may use a post-release, such as
`0.1.0.post1`. See [Python versioning](https://packaging.python.org/en/latest/specifications/version-specifiers/#post-releases).

## Documentation conventions

Keep the main README focused on the product, installation, first use, and
collaboration. Put maintainer procedures here, advanced settings in
[configuration](configuration.md), and implementation details in
[architecture](architecture.md).

The README also becomes the PyPI description. Use absolute HTTPS links for
documentation and publicly accessible image URLs; private GitHub raw assets
cannot be loaded by PyPI visitors. Verify Markdown rendering and distribution
metadata before publishing.
