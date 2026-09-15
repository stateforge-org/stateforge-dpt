# StateForge DPT

StateForge Development Progress Tracking is a local-first control application
for coordinating work across the StateForge repositories.

DPT edits the implementation plan locally, publishes its complete portable
manifest into an identical `STATEFORGE_DPT.md` in every managed repository, and
derives delivery progress from issues, pull requests, reviews, checks, and merge
evidence retrieved from GitHub.

## Run DPT

DPT is packaged for Linux, macOS, and Windows. Requirements are Python 3.11 or
newer and Git 2.31 or newer on PATH. Node.js is only needed when developing or
building a release, not when installing and running DPT.

Once the first release is published to PyPI:

```bash
python -m pip install stateforge-dpt
stateforge-dpt run
```

On macOS/Linux, use `python3` if that is your Python command. On Windows, you can
use `py -m pip install stateforge-dpt` and `py -m stateforge_dpt run` if the
console command is not on PATH. A virtual environment is recommended; do not use
`sudo pip install`. Before PyPI publication, install a downloaded wheel with
`python -m pip install /path/to/stateforge_dpt-0.1.0-py3-none-any.whl`.

Run `stateforge-dpt run` from any directory, then open
[http://127.0.0.1:8463](http://127.0.0.1:8463) in your browser. The command prints
the browser address and workspace locations at startup. Installed release wheels
include the frontend; no separate frontend server is needed.

By default, the SQLite working copy and encrypted GitHub credentials live in
`~/.stateforge-dpt/`. Suggested clones live under
`~/.stateforge-dpt/projects/<PROJECT_NAME>/<REPOSITORY_NAME>`.

User configuration is read from `~/.stateforge-dpt/.env`, with environment
variables taking precedence. Editable source installations also read the
checkout's `.env` as a lower-priority development configuration. Relative paths
in a configuration file are resolved beside that file, never against the launch
directory. Relative environment paths are resolved under `~/.stateforge-dpt/`.
See [.env.example](.env.example) for overrides.

Existing databases are not moved or overwritten. To open a previous workspace:

```bash
stateforge-dpt run --data-dir /absolute/path/to/previous/.stateforge-dpt
```

The `--working-directory` option changes the parent workspace for suggested
clones without changing the data directory.

## Development and packaging

```bash
python3.11 -m venv .venv
. .venv/bin/activate
cd frontend && npm install && npm run build && cd ..
python -m pip install -e .
stateforge-dpt run
```

Build the frontend before building a Python release (`python -m build`). Both
wheels and source distributions carry that build, so installing them does not
require Node.js. A missing frontend build produces an actionable startup error
instead of a server that returns 404 at its home page.

The package workflow builds the frontend, creates a wheel and source
distribution, checks their metadata, and installs the same wheel on Linux,
macOS, and Windows using Python 3.11–3.14. Each environment runs the test suite
and starts the installed console command from an unrelated directory to check
the frontend, JavaScript, CSS, favicon, API, and default data directory.

### Publish a release

Publishing is manual; ordinary pushes and pull requests never upload to PyPI.
Create the pending `stateforge-dpt` project in PyPI's Trusted Publishing settings
with owner `stateforge-org`, repository `stateforge-dpt`, workflow `publish.yml`,
and environment `pypi`. Configure the equivalent publisher on TestPyPI with
environment `testpypi`. Protect these GitHub environments with required reviewer
approval. No permanent PyPI token needs to be stored in the repository.

Then run **Publish pip package (manual)** in GitHub Actions, choosing TestPyPI
first. The workflow rebuilds and tests the distributions on all three operating
systems before publishing. Choose PyPI for the production release. Increment
the version in `pyproject.toml` for subsequent releases; PyPI versions cannot be
overwritten. See [PyPI's Trusted Publishing guide](https://docs.pypi.org/trusted-publishers/).

Select **Continue with GitHub** and authorize the StateForge DPT GitHub App
with the displayed one-time code. DPT uses GitHub Device Flow: no callback URL,
client secret, or user-managed token is required. Access and refresh tokens are
encrypted locally and never exposed to the browser.

## Core authority model

- Git-tracked `STATEFORGE_DPT.md` files are the portable project authority. An
  embedded versioned manifest preserves every repository, milestone, planned
  branch, dependency, ordering rule, project revision, and content fingerprint.
- SQLite is each computer's local editable working copy and GitHub evidence
  cache. DPT refuses to silently overwrite local edits or choose between
  divergent Git copies.
- Every project has an explicit plan source branch (for example,
  `mvp/implementation`). Reconciliation fetches that branch and reads only its
  `STATEFORGE_DPT.md` through Git; it never checks out, rebases, or modifies the
  developer's current branch or working tree.
- Importing any participating clone recreates the full project. Only that clone
  is attached immediately; the remaining repositories are ready to clone or
  attach on the new computer.
- Publishing writes the next identical revision to every attached clone. Commit
  and push those generated files normally, then merge them into the configured
  plan source branch. Reconciliation on another computer automatically imports
  a strictly newer, conflict-free revision into its local SQLite working copy.
- Local unpublished edits and same-revision divergence are never overwritten;
  DPT surfaces the existing source chooser so the user can resolve them.
- GitHub is the authority for delivery progress, not project definition.
- DPT validates GitHub evidence against the dependency graph and reports real
  workflow violations.

See [docs/architecture.md](docs/architecture.md) for the complete design.
