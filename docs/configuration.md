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
| `STATEFORGE_DPT_GITHUB_APP_ID` | App identity used only by Review & publish | Not configured |
| `STATEFORGE_DPT_GITHUB_APP_PRIVATE_KEY_PATH` | Local PEM used only to mint short-lived installation tokens | Not configured |
| `STATEFORGE_DPT_MCP_ENABLED` | Serve the local AI-agent interface | `true` |
| `STATEFORGE_DPT_MCP_PATH` | Streamable HTTP MCP endpoint | `/mcp` |
| `STATEFORGE_DPT_MCP_MAX_READ_BYTES` | Maximum UTF-8 bytes returned by one repository resource read | `128000` |
| `STATEFORGE_DPT_MCP_ALLOW_REMOTE` | Explicitly permit MCP with a non-loopback bind; requires your own authenticated boundary | `false` |

Environment variables override configuration-file values. Editable source
installations also read the checkout's `.env` as a lower-priority fallback.
Explicit run flags override the corresponding resolved settings.

Relative paths in a configuration file resolve beside that file. Relative paths
provided by environment variables resolve under `~/.stateforge-dpt/`, not the
directory from which DPT was started.

## AI-agent interface

On the default loopback bind, `stateforge-dpt run` serves MCP at
`http://127.0.0.1:8463/mcp` from the same process and port as the browser. Pass
`--no-mcp` when the agent interface is not wanted. If DPT is bound to a
non-loopback address, MCP is disabled automatically unless
`STATEFORGE_DPT_MCP_ALLOW_REMOTE=true` is explicitly configured. The remote
override does not add authentication; place a real authenticated and encrypted
boundary in front of DPT before enabling it.

MCP plan application is a consequential write tool. Configure the client to
ask for confirmation on every `apply_plan_change_proposal` call. A remembered
or blanket auto-approval defeats the human-authority boundary. See
[AI agent interface](ai-agent-interface.md) for the complete workflow.

Codex desktop users can install the matching personal local plugin and approval
policy with `stateforge-dpt setup-codex`. The command writes only the
StateForge-owned plugin directory, updates the StateForge entry in the personal
marketplace, and maintains a clearly marked block in `~/.codex/config.toml`.
Existing unrelated marketplace entries and Codex settings are preserved.

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

The project dashboard loads the latest successful GitHub Actions run for every
managed repository on the configured plan source branch. A custom GitHub App
therefore needs read access to **Actions** in addition to the repository access
used by reconciliation. Missing permission is isolated to that repository and
appears as unavailable CI status; it does not prevent the rest of the dashboard
from loading.

## Privileged plan publication

Ordinary GitHub activity always uses the connected human's Device Flow token.
Issues, pull requests, reviews, labels, reconciliation, and delivery evidence are
therefore attributed to that user. Only **Review & publish** authenticates as the
installed StateForge DPT GitHub App.

To enable publication:

1. Open the StateForge DPT GitHub App settings and generate a private key.
2. Store the downloaded PEM outside every repository. Restrict its filesystem
   permissions to the current user where the platform supports that control.
3. Add the App to the protected plan-source branch ruleset with **Always allow**
   bypass. Do not grant that bypass to the connected human merely for DPT.
4. Add the App ID and private-key path to `~/.stateforge-dpt/.env`:

   ```dotenv
   STATEFORGE_DPT_GITHUB_APP_ID=123456
   STATEFORGE_DPT_GITHUB_APP_PRIVATE_KEY_PATH=~/.stateforge-dpt/stateforge-dpt.pem
   ```

At publication time DPT uses the private key only to mint a one-hour App
installation token restricted to the current repository and `contents: write`.
It creates a commit containing only the configured generated plan file, omits
custom author/committer fields so GitHub can apply and verify the App's bot
signature, verifies that signature before moving the protected branch, and
never force-pushes. The installation token and private key are never placed in
Git configuration, command arguments, the database, or browser state.

## Shared plans and delivery labels

Set the project's **plan source branch** in **Settings**. Reconciliation fetches
`STATEFORGE_DPT.md` from that branch without checking it out or rebasing the
developer's branch.

After **Review & publish**, the verified App commit is already present on the
source branch. Other computers receive it through normal reconciliation.

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
