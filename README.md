<!--
# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2025 The Linux Foundation
-->

# 🔗 Test Gerrit Servers

This repository contains workflows to test connectivity and replication from
production Gerrit servers using the
[gerrit-action](https://github.com/modeseven-lfreleng-actions/gerrit-action).

## Purpose

- Check Gerrit server connectivity across LF-hosted instances
- Test pull-replication plugin functionality
- Verify authentication credentials work as expected
- Ensure API path detection works for each server
- Resync GitHub organizations with Gerrit content

## Workflow

The main workflow (`test-gerrit-deploy.yaml`) runs tests against each Gerrit
server defined in the `GERRIT_SERVERS` repository variable.

### Manual Trigger

You can trigger the workflow manually with optional parameters:

<!-- markdownlint-disable MD013 -->

| Input                    | Description                                                                                                                                                            | Default                                                                 |
| ------------------------ | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------- |
| `debug`                  | Enable verbose debug output                                                                                                                                            | `false`                                                                 |
| `sync_on_startup`        | Trigger pull-replication after container startup                                                                                                                       | `true`                                                                  |
| `skip_archived_projects` | Skip archived (`READ_ONLY`) Gerrit projects during initial sync (faster startup; uncheck to include archived projects for backfill/debug)                              | `true`                                                                  |
| `persist_project_slugs`  | Persistent session selector (project slug, see [Selector Syntax](#selector-syntax))                                                                                    | `onap`                                                                  |
| `persist_minutes`        | Persistent session duration (minutes, 0 to skip, max 600)                                                                                                              | `15`                                                                    |
| `match_api_path`         | Match origin server URL API path                                                                                                                                       | `true`                                                                  |
| `remote_access`          | Enable remote Gerrit access (`None`, `Bore (bore.pub)`, `Bore (modeseven.org)`, [`Tailscale`](#tailscale-setup))                                                       | `Tailscale`                                                             |
| `reset_orgs`             | Reset (delete all repos from) GitHub ORGs                                                                                                                              | `false`                                                                 |
| `sync_orgs`              | Sync GitHub ORGs from Gerrit                                                                                                                                           | `false`                                                                 |
| `sync_project_slugs`     | GitHub orgs to reset/sync (selector, see [Selector Syntax](#selector-syntax))                                                                                          | `all`                                                                   |
| `gerrit_clone_ref`       | Build gerrit-clone from git ref (`owner/repo@branch`); omit for PyPI                                                                                                   | `modeseven-lfreleng-actions/gerrit-clone-action@feat/content-filtering` |
| `gerrit_action_ref`      | Use gerrit-action from git ref (`owner/repo@branch`)                                                                                                                   | `modeseven-lfreleng-actions/gerrit-action@integration-updates`          |
| `g2p_install_ref`        | Optional git ref (branch, tag, SHA, or `refs/changes/<NN>/<num>/<ps>`) to install `gerrit_to_platform` from after the container starts; empty keeps the pinned release | `refs/changes/75/74275/2`                                               |
| `g2p_install_repo`       | Source HTTPS Git URL for `g2p_install_ref` (consulted when the ref input has a value)                                                                                  | `https://gerrit.linuxfoundation.org/infra/releng/gerrit_to_platform`    |

<!-- markdownlint-enable MD013 -->

### Selector Syntax

The `persist_project_slugs` and `sync_project_slugs` inputs support
flexible matching:

| Pattern        | Description               | Example                       |
| -------------- | ------------------------- | ----------------------------- |
| `all`          | Match all items (default) | `all`                         |
| Single value   | Exact match               | `onap`                        |
| List of values | Comma or space-separated  | `onap, oran` or `onap oran`   |
| Wildcards      | Shell-style patterns      | `*gerrit*`, `modeseven-?ran*` |

**Examples:**

```bash
# Match ONAP servers/orgs
persist_project_slugs: "onap"

# Match specific servers
persist_project_slugs: "onap, oran, lf"

# Match all gerrit-prefixed GitHub orgs
sync_project_slugs: "*gerrit*"

# Match ONAP and O-RAN-SC orgs (both regular and gerrit variants)
sync_project_slugs: "modeseven-onap, modeseven-gerrit-onap, modeseven-o-ran-sc"
```

### Reset and Sync GitHub Organizations

The `reset_orgs` and `sync_orgs` inputs control GitHub organization
lifecycle operations:

- **`reset_orgs`**: Delete all repositories from the target GitHub
  organization(s).
- **`sync_orgs`**: Mirror all content from the corresponding Gerrit
  server and push to GitHub (respecting any
  [project filters](#project-filtering) configured in
  `GERRIT_SERVERS`).

You can enable either operation independently or both together. When
you enable both, reset runs first and sync follows. These jobs run **in
parallel** with the Gerrit server deployment — they share no
dependencies and do not block each other.

This uses the [gerrit-clone][gerrit-clone] CLI tool to perform bulk
operations. The `gerrit_clone_ref` input controls which version of
the tool to install (from a git ref or from PyPI).

**⚠️ WARNING**: `reset_orgs` is a destructive operation! It deletes
all existing repositories in the target GitHub organizations.

Use `sync_project_slugs` to select specific organizations:

```bash
# Sync ONAP organizations
sync_orgs: true
sync_project_slugs: "*onap*"

# Reset and sync gerrit-specific organizations
reset_orgs: true
sync_orgs: true
sync_project_slugs: "*gerrit*"
```

## Configuration

### Repository Variables

#### `GERRIT_SERVERS` (Required)

A JSON array defining the Gerrit servers to test and mirror. Each server
object should contain:

<!-- markdownlint-disable MD013 -->

| Field               | Required | Description                                                                  |
| ------------------- | -------- | ---------------------------------------------------------------------------- |
| `name`              | Yes      | Display name for the server (e.g., "Linux Foundation")                       |
| `slug`              | Yes      | Short identifier used for container naming and credential lookup             |
| `gerrit`            | Yes      | Gerrit server hostname (e.g., "gerrit.linuxfoundation.org")                  |
| `api_path`          | No       | API path prefix if not at root (e.g., "/infra", "/r")                        |
| `project_filter`    | No       | Regex pattern to filter projects, empty = all projects                       |
| `github_org`        | No       | Target GitHub org for standard workflows                                     |
| `github_gerrit_org` | No       | Target GitHub org for gerrit_to_platform integrations                        |
| `include_projects`  | No       | Include filter for mirror sync (see [Project Filtering](#project-filtering)) |
| `exclude_projects`  | No       | Exclude filter for mirror sync (see [Project Filtering](#project-filtering)) |

<!-- markdownlint-enable MD013 -->

**Example (use this verbatim as your repository variable):**

```json
[
  {
    "name": "Linux Foundation",
    "slug": "lf",
    "gerrit": "gerrit.linuxfoundation.org",
    "api_path": "/infra",
    "project_filter": "",
    "github_org": "modeseven-lf",
    "github_gerrit_org": "modeseven-gerrit-lf"
  },
  {
    "name": "ONAP",
    "slug": "onap",
    "gerrit": "gerrit.onap.org",
    "api_path": "/r",
    "project_filter": "",
    "github_org": "modeseven-onap",
    "github_gerrit_org": "modeseven-gerrit-onap",
    "exclude_projects": "testsuite/pythonsdk-tests"
  },
  {
    "name": "O-RAN-SC",
    "slug": "oran",
    "gerrit": "gerrit.o-ran-sc.org",
    "api_path": "/r",
    "project_filter": "",
    "github_org": "modeseven-o-ran-sc",
    "github_gerrit_org": "modeseven-gerrit-o-ran-sc"
  },
  {
    "name": "OpenDaylight",
    "slug": "opendaylight",
    "gerrit": "git.opendaylight.org",
    "api_path": "/gerrit",
    "project_filter": "",
    "github_org": "modeseven-opendaylight",
    "github_gerrit_org": "modeseven-gerrit-opendaylight"
  }
]
```

#### Project Filter Examples

The `project_filter` field supports:

- **Empty string** (`""`): Replicate ALL projects from the server
- **Literal name**: Match exact project name (e.g., `"releng/lftools"`)
- **Comma-separated**: Two or more projects (e.g., `"releng/lftools,ci-management"`)
- **Regex pattern**: Match projects by pattern (e.g., `"releng/.*"`)

### Project Filtering

The `include_projects` and `exclude_projects` fields control which
Gerrit projects the workflow mirrors to GitHub during sync
operations. Both fields accept comma or space-separated lists of
Gerrit project names. When you specify both, the workflow applies
include filters first, then exclude filters remove matches from
the result.

The workflow threads these fields through the matrix to the
`gerrit-clone mirror` command as `--include-projects` /
`--exclude-projects` arguments. They support the same pattern
syntax as the
[gerrit-clone filtering engine][gerrit-clone]:

<!-- markdownlint-disable MD013 -->

| Pattern         | Description                        | Example                      |
| --------------- | ---------------------------------- | ---------------------------- |
| Exact name      | Matches one project                | `testsuite/pythonsdk-tests`  |
| Hierarchical    | Matches project and all children   | `ccsdk` matches `ccsdk/apps` |
| Wildcards       | Shell-style `*`, `?`, `[seq]`      | `testsuite/*`                |
| Comma-separated | Two or more patterns in one string | `ccsdk, oom, cps`            |

<!-- markdownlint-enable MD013 -->

**Examples:**

```jsonc
// Exclude a project containing a leaked credential
{ "exclude_projects": "testsuite/pythonsdk-tests" }

// Mirror specific project hierarchies
{ "include_projects": "ccsdk, oom, cps" }

// Include a broad set, then carve out exceptions
{
  "include_projects": "testsuite",
  "exclude_projects": "testsuite/pythonsdk-tests"
}
```

> **Note:** When `include_projects` is empty (the default), the
> workflow mirrors all discovered projects. When `exclude_projects`
> is empty, the workflow skips nothing. Servers without either field
> mirror all projects, preserving existing behaviour.

### Repository Secrets

#### `GERRIT_CREDENTIALS` (Required)

A **base64-encoded** JSON array containing credentials for each Gerrit server.
Each entry must have a `slug` that matches the corresponding entry in
`GERRIT_SERVERS`.

> **Important:** The secret must be base64-encoded to prevent GitHub from
> applying spurious redactions to the console output. GitHub's secret masking
> can interfere with JSON structures containing passwords.

**Step 1: Create a JSON file with your credentials:**

```json
[
  {
    "slug": "lf",
    "username": "your-lf-username",
    "password": "XXXXXXXX"
  },
  {
    "slug": "onap",
    "username": "your-onap-username",
    "password": "XXXXXXXX"
  },
  {
    "slug": "oran",
    "username": "your-oran-username",
    "password": "XXXXXXXX"
  },
  {
    "slug": "opendaylight",
    "username": "your-odl-username",
    "password": "XXXXXXXX"
  }
]
```

**Step 2: Base64-encode the JSON:**

```bash
# On Linux/macOS:
cat credentials.json | base64

# Or inline:
echo '[{"slug":"lf","username":"user","password":"pass"}]' | base64
```

**Step 3: Store the base64-encoded string as the `GERRIT_CREDENTIALS` secret.**

The workflow will automatically decode the base64 content before parsing the
JSON.

**Notes:**

- The password should be the HTTP password from your Gerrit account settings,
  not your SSO/login password
- The `slug` values must match in `GERRIT_SERVERS` and `GERRIT_CREDENTIALS`
- Ensure there are no trailing newlines in your base64 encoding (use
  `base64 -w 0` on Linux if needed)

#### `TS_OAUTH_CLIENT_ID` and `TS_OAUTH_SECRET` (Required for Tailscale)

The workflow reads these secrets when you select **Tailscale** for
`remote_access`. They authenticate the GitHub Actions runner as an
ephemeral node on your [Tailscale](https://tailscale.com/) network
(tailnet), giving every device on the tailnet direct access to the
Gerrit server's HTTP and SSH ports — no public exposure, no port
translation.

See [Tailscale Setup](#tailscale-setup) below for full instructions
on creating these credentials.

#### `ACTIONS_STEP_DEBUG` (Optional)

When set to `true`, enables debug output for the workflow. The
validation job checks this alongside the `debug` input and GitHub's
built-in `RUNNER_DEBUG` flag — if any of the three is active, the
workflow produces verbose logging.

#### `ORG_ADMIN_TOKEN` (Required for reset/sync)

A GitHub Personal Access Token (Classic) with the following
permissions:

- `repo` — Full control of private repositories
- `delete_repo` — Delete repositories
- `admin:org` — Full control of organizations

The reset and sync jobs use this token to delete and recreate
repositories in the target GitHub organizations.

### Repository Variables (continued)

#### `GERRIT_PUBLIC_KEYS` (Optional)

SSH public keys passed to the `gerrit-action` for configuring SSH
access on the local Gerrit container. Set this as a repository
variable (not a secret) containing one or more SSH public keys.

## Outputs

Each deploy job outputs:

- Container connectivity status
- API path detection results
- Replication status (when `sync_on_startup` option set)
- SSH host keys for the local Gerrit container
- Connection info with URLs and SSH commands (in job summary)

Reset jobs output:

- Per-organization reset status

Sync jobs output:

- Mirror manifest with total/succeeded/failed counts
- Active project filters (if configured)
- Per-organization sync status with Gerrit server details

## Tailscale Setup

Selecting **Tailscale** for the `remote_access` workflow input causes
the runner to join your tailnet through the
[tailscale/github-action](https://github.com/tailscale/github-action).
Unlike Bore (which exposes random ports on the public internet),
Tailscale restricts access to devices on your private tailnet.

### Prerequisites

- A [Tailscale](https://tailscale.com/) account with **Owner** or
  **Admin** permissions on the tailnet.
- At least one other device on the same tailnet (e.g. your laptop)
  to reach the Gerrit server after it starts.

### Step 1 — Define `tag:ci` in your ACL policy

The OAuth client must associate nodes with at least one
[tag](https://tailscale.com/kb/1068/acl-tags). Open **Access controls**
in the
[Tailscale admin console](https://login.tailscale.com/admin/acls/file)
and add a `tag:ci` entry to the `tagOwners` section:

```jsonc
{
  "tagOwners": {
    "tag:ci": ["autogroup:admin"]
  }
}
```

If you already have a `tagOwners` block, merge the new entry into it.

You should also ensure your ACL grants (or the default allow-all
policy) permit your devices to reach `tag:ci` nodes on the relevant
ports. With the default policy this works automatically. If you have
a restrictive policy, add a grant such as:

```jsonc
{
  "grants": [{
    "src": ["autogroup:member"],
    "dst": ["tag:ci"],
    "ip": ["*"]
  }]
}
```

### Step 2 — Create an OAuth client

1. Open the **Trust credentials** page:
   <https://login.tailscale.com/admin/settings/oauth>
2. Click **Credential** → select **OAuth**.
3. In the scopes table, find **Auth Keys** and set it to **Write**.
4. Under **Tags**, select `tag:ci` (the tag you created in Step 1).
5. Click **Generate credential**.
6. **Copy both the Client ID and Client Secret right away.**
   Tailscale displays the secret once; closing the dialog hides it
   permanently.

### Step 3 — Store the credentials as GitHub secrets

| Secret name          | Value                               |
| -------------------- | ----------------------------------- |
| `TS_OAUTH_CLIENT_ID` | The OAuth **Client ID** from Step 2 |
| `TS_OAUTH_SECRET`    | The OAuth **Secret** from Step 2    |

Add them at: **Repository → Settings → Secrets and variables →
Actions → New repository secret**.

### How it works at runtime

When a workflow run selects **Tailscale**:

1. The `tailscale/github-action` step uses the OAuth credentials to
   register the runner as an **ephemeral, pre-approved** node tagged
   `tag:ci`.
2. The runner receives a Tailscale IP (e.g. `100.x.y.z`).
3. The workflow feeds that IP and the local Gerrit ports to
   `gerrit-action` as `tunnel_host` / `tunnel_ports`, which
   configures Gerrit's `canonicalWebUrl` and
   `sshd.advertisedAddress`.
4. Any device on your tailnet can then reach the Gerrit web UI and
   SSH directly via the Tailscale IP.
5. When the job finishes (or you cancel it), the action's post-step
   logs the node out and the tailnet removes it automatically.

### Recreating with a different account or tailnet

To point this workflow at a different Tailscale account:

1. Repeat Steps 1–3 above using the new account's admin console.
2. Update the two repository secrets (`TS_OAUTH_CLIENT_ID`,
   `TS_OAUTH_SECRET`) with the new values.
3. You do not need to change any workflow files — the secrets use
   generic names that work with any tailnet.

The workflow step also sets a deterministic hostname
(`gerrit-ci-<slug>`) so the node is easy to identify in the
Tailscale admin console while the job is running.

## Related

- [gerrit-action](https://github.com/modeseven-lfreleng-actions/gerrit-action) -
  The GitHub Action used by this workflow
- [gerrit-clone-action][gerrit-clone] - Bulk clone/mirror tool for Gerrit
- [Gerrit Code Review](https://www.gerritcodereview.com/) - Gerrit documentation
- [pull-replication plugin][pull-replication] - Plugin documentation

[gerrit-clone]: https://github.com/lfreleng-actions/gerrit-clone-action
[pull-replication]: https://gerrit.googlesource.com/plugins/pull-replication

## License

Apache-2.0
