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

| Input             | Description                                                                   | Default |
| ----------------- | ----------------------------------------------------------------------------- | ------- |
| `debug`           | Enable verbose debug output                                                   | `false` |
| `sync_on_startup` | Trigger pull-replication after container startup                              | `true`  |
| `persist_project_slugs` | Persistent session selector (project slug, see [Selector Syntax](#selector-syntax)) | `onap`  |
| `persist_minutes`  | Persistent session duration (minutes, max 60)                                 | `15`    |
| `match_api_path`  | Match origin server URL API path                                              | `true`  |
| `remote_access`   | Enable remote Gerrit access (`None`, `Bore`, [`Tailscale`](#tailscale-setup)) | `None`  |
| `resync_orgs`     | Resync GitHub ORGs with Gerrit content                                        | `false` |
| `resync_project_slugs` | Resync these GitHub orgs (slug, see [Selector Syntax](#selector-syntax))      | `all`   |

<!-- markdownlint-enable MD013 -->

### Selector Syntax

Both `persist_project_slugs` and `resync_project_slugs` inputs support flexible matching:

| Pattern | Description | Example |
| ------- | ----------- | ------- |
| `all` | Match all items (default) | `all` |
| Single value | Exact match | `onap` |
| List of values | Comma or space-separated | `onap, oran` or `onap oran` |
| Wildcards | Shell-style patterns | `*gerrit*`, `modeseven-?ran*` |

**Examples:**

```bash
# Match ONAP servers/orgs
persist_project_slugs: "onap"

# Match specific servers
persist_project_slugs: "onap, oran, lf"

# Match all gerrit-prefixed GitHub orgs
resync_project_slugs: "*gerrit*"

# Match ONAP and O-RAN-SC orgs (both regular and gerrit variants)
resync_project_slugs: "modeseven-onap, modeseven-gerrit-onap, modeseven-o-ran-sc"
```

### Resync GitHub Organizations

When you enable `resync_orgs`, the workflow will:

1. **Reset**: Delete all repositories from the target GitHub organization(s)
2. **Mirror**: Clone all content from the corresponding Gerrit server and push
   to GitHub

This uses the [gerrit-clone](https://github.com/lfreleng-actions/gerrit-clone-action)
CLI tool (invoked via `uvx gerrit-clone`) to perform bulk operations. The
resync jobs run **completely in parallel** with the Gerrit server deployment
and sync/test jobs — they share no dependencies and do not block each other.

**⚠️ WARNING**: This is a destructive operation! The workflow deletes
all existing repositories in the target GitHub organizations before mirroring.

Use `resync_project_slugs` to select specific organizations:

```bash
# Resync ONAP organizations
resync_orgs: true
resync_project_slugs: "*onap*"

# Resync gerrit-specific organizations
resync_orgs: true
resync_project_slugs: "*gerrit*"
```

## Configuration

### Repository Variables

#### `GERRIT_SERVERS` (Required)

A JSON array defining the Gerrit servers to test. Each server object should
contain:

<!-- markdownlint-disable MD013 -->

| Field | Required | Description |
| ----- | -------- | ----------- |
| `name` | Yes | Display name for the server (e.g., "Linux Foundation") |
| `slug` | Yes | Short identifier used for container naming and credential lookup |
| `gerrit` | Yes | Gerrit server hostname (e.g., "gerrit.linuxfoundation.org") |
| `api_path` | No | API path prefix if not at root (e.g., "/infra", "/r") |
| `project_filter` | No | Regex pattern to filter projects, empty = all projects |
| `github_org` | No | Target GitHub org for standard workflows |
| `github_gerrit_org` | No | Target GitHub org for gerrit_to_platform integrations |

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
