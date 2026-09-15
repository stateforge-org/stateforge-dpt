# Configuration and troubleshooting

The default installation needs no configuration file. Run `stateforge-dpt run`
from any directory and open the address printed in the terminal.

## Workspace locations

By default, DPT keeps its SQLite working copy, cached GitHub evidence, and
encrypted credentials in `~/.stateforge-dpt/`. New repository clones are suggested
under `~/.stateforge-dpt/projects/<PROJECT_NAME>/<REPOSITORY_NAME>`.

These locations belong to the current computer. Only the portable plan in
`STATEFORGE_DPT.md` is shared through Git.

### Use an existing data directory

Existing databases are not moved or overwritten. To open a workspace stored
outside the default location, pass its directory explicitly:

```sh
stateforge-dpt run --data-dir /path/to/existing/dpt-data
```

For an older source-checkout installation, this may be the checkout's
`.stateforge-dpt` directory. On Windows, quote paths containing spaces.

### Choose a clone workspace

The working-directory option changes the parent workspace used for new clones:

```sh
stateforge-dpt run --working-directory /path/to/workspace
```

Suggested clones then live under
`/path/to/workspace/projects/<PROJECT_NAME>/<REPOSITORY_NAME>`. This option does
not change the SQLite data directory.

### Choose a different port

```sh
stateforge-dpt run --port 8471
```

Open the new address printed by DPT. Use `stateforge-dpt run --help` for the
complete list of run options.

## Configuration file and environment variables

Save optional configuration in `~/.stateforge-dpt/.env`. For example:

```dotenv
STATEFORGE_DPT_PORT=8471
STATEFORGE_DPT_WORKING_DIRECTORY=~/Development
```

| Variable | Purpose | Default |
| --- | --- | --- |
| `STATEFORGE_DPT_HOST` | Listening address | `127.0.0.1` |
| `STATEFORGE_DPT_PORT` | Listening port | `8463` |
| `STATEFORGE_DPT_DATA_DIR` | Database and credential directory | `~/.stateforge-dpt` |
| `STATEFORGE_DPT_WORKING_DIRECTORY` | Parent workspace for clones | Resolved data directory |
| `STATEFORGE_DPT_FRONTEND_DIR` | Override the browser interface build | Included package assets |
| `STATEFORGE_DPT_GITHUB_CLIENT_ID` | Use a different GitHub App | Built-in StateForge DPT App |

Environment variables override configuration-file values. Editable source
installations also read the checkout's `.env` as a lower-priority fallback.
Explicit run flags override the corresponding resolved settings.

Relative paths in a configuration file resolve beside that file. Relative paths
provided by environment variables resolve under `~/.stateforge-dpt/`, not the
directory from which DPT was started.

## Connect GitHub

Select **Continue with GitHub** on the welcome page, or use **Connect with
GitHub** inside a project. Enter the displayed one-time code on GitHub and
approve the requested access.

DPT uses GitHub Device Flow. Users do not need to configure a callback URL,
client secret, or personal access token. Access and refresh tokens are encrypted
on the local computer; the browser receives an HTTP-only session cookie, not
the GitHub token.

Each collaborator must connect their own account. Local mode supports plan
editing and locally cached views, but does not refresh GitHub delivery evidence.

GitHub writes depend on the connected account and App having access to the
repository and the required permissions. A `403` is an authorization failure,
not a reason to keep retrying unchanged credentials. A PR also needs distinct
base and compare branches with commits to propose; creating a branch alone does
not create a meaningful code difference.

## Shared plans and delivery labels

Set the project's **plan source branch** in **Settings**. Reconciliation fetches
`STATEFORGE_DPT.md` from that branch without checking it out or rebasing the
developer's branch.

After **Review & publish**, commit and push the generated files and merge them
into the source branch. Other computers cannot fetch changes that exist only
in your local working tree or on an unmerged feature branch.

Importing a participating clone recreates the complete plan. Only that clone's
local path is attached immediately; attach or clone the remaining repositories
on the new computer. Credentials, local paths, and cached GitHub conversations
are not imported from the portable file.

Existing GitHub issues and PRs must carry the matching DPT labels to contribute
to a step's observed progress. For example:

```text
dpt:project:stateforge
dpt:release:mvp
dpt:step:P1
```

Use the values for your own project, release, and step. Read
[the architecture guide](architecture.md) for matching and conflict rules.

## Common startup problems

- **`stateforge-dpt` is not found:** activate the environment where it was
  installed, or use `python -m stateforge_dpt run`. On Windows, use
  `py -m stateforge_dpt run` with the same Python installation used for pip.
- **pip refuses an OS-managed Python:** install in a virtual environment, or use
  [pipx](https://pipx.pypa.io/). Do not use `sudo pip install` to bypass the error.
- **Git is not found:** install Git 2.31 or newer and ensure it is available on
  the server process's PATH.
- **The listening port is already in use:** choose another port with `--port`.
- **The frontend build is missing:** install the PyPI package into the intended
  environment. Source developers must build `frontend` first; see
  [the development guide](development.md).
- **An imported plan appears out of date:** check the source branch setting and
  confirm the published files were pushed and merged there. Resolve any source
  warnings or unpublished local changes in **Plan editor**.
