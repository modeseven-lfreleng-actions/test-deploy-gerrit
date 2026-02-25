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

| Input                     | Description                                                                           | Default |
| ------------------------- | ------------------------------------------------------------------------------------- | ------- |
| `debug`                   | Enable verbose debug output                                                           | `false` |
| `sync_on_startup`         | Trigger pull-replication after container startup                                      | `true`  |
| `persist_project_gerrits` | Persistent session selector (project Gerrit, see [Selector Syntax](#selector-syntax)) | `onap`  |
| `persist_minutes`         | Persistent session duration (minutes, max 25)                                         | `15`    |
| `match_api_path`          | Match origin server URL API path                                                      | `true`  |
| `remote_access`           | Enable remote Gerrit access (`None`, `Bore`, [`Tailscale`](#tailscale-setup))         | `None`  |
| `resync_github_orgs`      | Resync GitHub orgs (`none`, `all`, or substring filter)                               | `none`  |
| `gerrit_clone_ref`        | Build gerrit-clone from git ref (unset defaults to PyPI via `uvx`)                    | *fork*  |

<!-- markdownlint-enable MD013 -->

### Selector Syntax

#### `persist_project_gerrits`

Selects which Gerrit servers get persistent debug sessions. Matches against
the `slug` field in `GERRIT_SERVERS`.

| Pattern        | Description              | Example                       |
| -------------- | ------------------------ | ----------------------------- |
| `none`         | Disable persist sessions | `none`                        |
| `all`          | Match all servers        | `all`                         |
| Single value   | Exact match              | `onap`                        |
| List of values | Comma or space-separated | `onap, oran` or `onap oran`   |
| Wildcards      | Shell-style patterns     | `*gerrit*`, `modeseven-?ran*` |

**Examples:**

```bash
# Match ONAP server
persist_project_gerrits: "onap"

# Match specific servers
persist_project_gerrits: "onap, oran, lf"

# Match all servers with wildcard
persist_project_gerrits: "all"
```

#### `resync_github_orgs`

Selects which GitHub organizations to resync with Gerrit content. The workflow
matches values as **substrings** against the `github_org` and
`github_gerrit_org` fields in `GERRIT_SERVERS`.

<!-- markdownlint-disable MD013 -->

| Pattern        | Description                | Example                                      |
| -------------- | -------------------------- | -------------------------------------------- |
| `none`         | No resync (default)        | `none`                                       |
| `all`          | Resync all organizations   | `all`                                        |
| Single value   | Substring match            | `onap` (matches `modeseven-onap` etc.)       |
| List of values | Comma or space-separated   | `onap, o-ran-sc` or `onap o-ran-sc`          |

<!-- markdownlint-enable MD013 -->

**Examples:**

```bash
# Resync all ONAP-related organizations (matches modeseven-onap,
# modeseven-gerrit-onap, etc.)
resync_github_orgs: "onap"

# Resync ONAP and O-RAN-SC organizations
resync_github_orgs: "onap, o-ran-sc"

# Resync all organizations
resync_github_orgs: "all"
```

### Resync GitHub Organizations

When you set `resync_github_orgs` to anything other than `none`, the workflow will:

1. **Reset**: Delete all repositories from the target GitHub organization(s)
2. **Mirror**: Clone all content from the corresponding Gerrit server and push
   to GitHub

This uses the [gerrit-clone](https://github.com/lfreleng-actions/gerrit-clone-action)
CLI tool (invoked via `uvx gerrit-clone`) to perform bulk operations. The
resync jobs run **in parallel** with the Gerrit server deployment and
sync/test jobs — they share no dependencies and do not block each other.

**⚠️ WARNING**: This is a destructive operation! The workflow deletes
all existing repositories in the target GitHub organizations before mirroring.

Use `resync_github_orgs` to select specific organizations:

```bash
# Resync ONAP organizations (substring match)
resync_github_orgs: "onap"

# Resync gerrit-specific organizations
resync_github_orgs: "gerrit"
```

## Configuration

### Repository Variables

#### `GERRIT_SERVERS` (Required)

A JSON array defining the Gerrit servers to test. Each server object should
contain:

<!-- markdownlint-disable MD013 -->

| Field               | Required | Description                                                      |
| ------------------- | -------- | ---------------------------------------------------------------- |
| `name`              | Yes      | Display name for the server (e.g., "Linux Foundation")           |
| `slug`              | Yes      | Short identifier used for container naming and credential lookup |
| `gerrit`            | Yes      | Gerrit server hostname (e.g., "gerrit.linuxfoundation.org")      |
| `api_path`          | No       | API path prefix if not at root (e.g., "/infra", "/r")            |
| `project_filter`    | No       | Regex pattern to filter projects, empty = all projects           |
| `github_org`        | No       | Target GitHub org for standard workflows                         |
| `github_gerrit_org` | No       | Target GitHub org for gerrit_to_platform integrations            |

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
    "github_gerrit_org": "modeseven-gerrit-onap"
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

#### `ORG_ADMIN_TOKEN` (Required for resync)

A GitHub Personal Access Token (Classic) with the following permissions:

- `repo` - Full control of private repositories
- `delete_repo` - Delete repositories
- `admin:org` - Full control of organizations (for creating repos in orgs)

The resync job uses this token to delete and recreate repositories in
the target GitHub organizations.

## Outputs

Each test job outputs:

- Container connectivity status
- API path detection results
- Replication status (when `sync_on_startup` option set)
- SSH host keys for the local Gerrit container

Resync jobs output:

- Reset status (repositories deleted)
- Mirror manifest with success/failure counts
- Duration and per-repository status

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
