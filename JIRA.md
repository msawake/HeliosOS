# Jira & Bitbucket Integration

## Repository
- **Bitbucket**: `git@bitbucket.org:i2tic/ally-partner.git`
- **Jira Project**: [MS_Awake_ESMSPR12148](https://makingscience.atlassian.net/browse/PR12148)

## Default Time Logging
- **Default task**: `PR12148-1` (Awake - Internal Meetings)
- For meetings, check the calendar event description — if it contains a Jira link, log time there instead.

## Branch Naming Convention

Branches must reference the Jira ticket key:

```
<type>/PR12148-<number>-<short-description>
```

### Types
- `feat/` — new feature
- `fix/` — bug fix
- `chore/` — maintenance, config, dependencies
- `refactor/` — code restructuring without behavior change
- `docs/` — documentation only

### Examples
```
feat/PR12148-42-add-calendar-sync
fix/PR12148-15-fix-mlx-timeout
chore/PR12148-7-update-dependencies
```

## Commit Messages

Use conventional commits with the ticket reference:

```
<type>: <description> [PR12148-<number>]
```

The description should include the Jira ticket title.

### Examples
```
feat: add Google Calendar MCP integration [PR12148-42]
fix: resolve streaming timeout on 4B model [PR12148-15]
```

## Task Creation

- **Summary prefix**: Always prepend `AllyPartner - ` to the task summary (e.g., `AllyPartner - Fix streaming timeout`)
- **Assignee**: Assign to me unless told otherwise
- **Components**: Always set to `Ally Partner`

## Pull Requests

- PR title should include the Jira ticket: `[PR12148-42] Add calendar sync`
- Bitbucket will auto-link PRs to Jira when the ticket key appears in the branch name or PR title
