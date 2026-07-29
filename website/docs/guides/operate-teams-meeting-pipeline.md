---
title: "Operate the Teams Meeting Pipeline"
description: "Runbook, go-live checklist, and operator worksheet for the Microsoft Teams meeting pipeline"
---

# Operate the Teams Meeting Pipeline

Use this guide after you have already enabled the feature from [Teams Meetings](/docs/user-guide/messaging/teams-meetings).

This page covers:
- operator CLI flows
- routine subscription maintenance
- failure triage
- go-live checks
- rollout worksheet

## Core Operator Commands

### Validate the config snapshot

```bash
vermes teams-pipeline validate
```

Use this first after any config change.

### Inspect token health

```bash
vermes teams-pipeline token-health
vermes teams-pipeline token-health --force-refresh
```

Use `--force-refresh` when you suspect stale auth state.

### Inspect subscriptions

```bash
vermes teams-pipeline subscriptions
```

### Renew near-expiry subscriptions

```bash
vermes teams-pipeline maintain-subscriptions
vermes teams-pipeline maintain-subscriptions --dry-run
```

### Automating subscription renewal (REQUIRED for production)

**Microsoft Graph subscriptions expire in at most 72 hours.** If nothing renews them, meeting notifications silently stop after 3 days and the pipeline looks "broken." This is the #1 operational failure mode for any Graph-backed integration.

You MUST run `maintain-subscriptions` on a schedule. Pick one of these three options:

#### Option 1: Vermes cron (recommended if you already run the Vermes gateway)

Vermes ships a built-in cron scheduler. The `--no-agent` mode runs a script as the job (rather than using an LLM), and `--script` must point at a file under `~/.vermes/scripts/`. First create the script:

```bash
mkdir -p ~/.vermes/scripts
cat > ~/.vermes/scripts/maintain-teams-subscriptions.sh <<'EOF'
#!/usr/bin/env bash
exec vermes teams-pipeline maintain-subscriptions
EOF
chmod +x ~/.vermes/scripts/maintain-teams-subscriptions.sh
```

Then register a script-only cron job that runs every 12 hours (gives 6x headroom against the 72h expiry window):

```bash
vermes cron create "0 */12 * * *" \
  --name "teams-pipeline-maintain-subscriptions" \
  --no-agent \
  --script maintain-teams-subscriptions.sh \
  --deliver local
```

Verify it was registered and inspect the next run time:

```bash
vermes cron list
vermes cron status        # scheduler status
```

#### Option 2: systemd timer (recommended for Linux production deployments)

Create `/etc/systemd/system/vermes-teams-pipeline-maintain.service`:

```ini
[Unit]
Description=Vermes Teams pipeline subscription maintenance
After=network-online.target

[Service]
Type=oneshot
User=vermes
EnvironmentFile=/etc/vermes/env
ExecStart=/usr/local/bin/vermes teams-pipeline maintain-subscriptions
```

And `/etc/systemd/system/vermes-teams-pipeline-maintain.timer`:

```ini
[Unit]
Description=Run Vermes Teams pipeline subscription maintenance every 12 hours

[Timer]
OnBootSec=5min
OnUnitActiveSec=12h
Persistent=true

[Install]
WantedBy=timers.target
```

Enable:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now vermes-teams-pipeline-maintain.timer
systemctl list-timers vermes-teams-pipeline-maintain.timer
```

#### Option 3: Plain crontab

```cron
0 */12 * * * /usr/local/bin/vermes teams-pipeline maintain-subscriptions >> /var/log/vermes/teams-pipeline-maintain.log 2>&1
```

Make sure the cron environment has the `MSGRAPH_*` credentials. Simplest fix: source `~/.vermes/.env` at the top of a wrapper script that crontab calls.

#### Verifying renewal is working

After you've set up the schedule, check renewal activity after the first scheduled run:

```bash
vermes teams-pipeline subscriptions   # should show expirationDateTime advanced
vermes teams-pipeline maintain-subscriptions --dry-run   # should show "0 expiring soon" most of the time
```

If you ever see your Graph webhook mysteriously "stop working" after exactly ~72 hours, this is the first thing to check: did the renewal job actually run?

### Inspect recent jobs

```bash
vermes teams-pipeline list
vermes teams-pipeline list --status failed
vermes teams-pipeline show <job-id>
```

### Replay a stored job

```bash
vermes teams-pipeline run <job-id>
```

### Dry-run meeting artifact fetches

```bash
vermes teams-pipeline fetch --meeting-id <meeting-id>
vermes teams-pipeline fetch --join-web-url "<join-url>"
```

## Routine Runbook

### After first setup

Run these in order:

```bash
vermes teams-pipeline validate
vermes teams-pipeline token-health --force-refresh
vermes teams-pipeline subscriptions
```

Then trigger or wait for a real meeting event and confirm:

```bash
vermes teams-pipeline list
vermes teams-pipeline show <job-id>
```

### Daily or periodic checks

- run `vermes teams-pipeline maintain-subscriptions --dry-run`
- inspect `vermes teams-pipeline list --status failed`
- verify the Teams delivery target is still the correct chat or channel

### Before changing webhook URLs or delivery targets

- update the public notification URL or Teams target config
- run `vermes teams-pipeline validate`
- renew or recreate affected subscriptions
- confirm new events land in the expected sink

## Failure Triage

### No jobs are being created

Check:
- `msgraph_webhook` is enabled
- the public notification URL points to `/msgraph/webhook`
- the client state in the subscription matches `MSGRAPH_WEBHOOK_CLIENT_STATE`
- subscriptions still exist remotely and are not expired

### Jobs stay in retry or fail before summarization

Check:
- transcript permissions and availability
- recording permissions and artifact availability
- `ffmpeg` availability if recording fallback is enabled
- Graph token health

### Summaries are produced but not delivered to Teams

Check:
- `platforms.teams.enabled: true`
- `delivery_mode`
- `incoming_webhook_url` for webhook mode
- `chat_id` or `team_id` plus `channel_id` for Graph mode
- Teams auth config if Graph posting is used

### Duplicate or unexpected replays

Check:
- whether you manually replayed a job with `vermes teams-pipeline run`
- whether the sink record already exists for that meeting
- whether you intentionally enabled a resend path in your local config

## Go-Live Checklist

- [ ] Graph credentials are present and correct
- [ ] `msgraph_webhook` is enabled and reachable from the public internet
- [ ] `MSGRAPH_WEBHOOK_CLIENT_STATE` is set and matches subscriptions
- [ ] transcript subscription is created
- [ ] recording subscription is created if STT fallback is required
- [ ] `ffmpeg` is installed if recording fallback is enabled
- [ ] Teams outbound delivery target is configured and verified
- [ ] Notion and Linear sinks are configured only if actually needed
- [ ] `vermes teams-pipeline validate` returns an OK snapshot
- [ ] `vermes teams-pipeline token-health --force-refresh` succeeds
- [ ] **`maintain-subscriptions` is scheduled** (Vermes cron, systemd timer, or crontab — see [Automating subscription renewal](#automating-subscription-renewal-required-for-production)). Without this, Graph subscriptions silently expire within 72 hours.
- [ ] a real end-to-end meeting event has produced a stored job
- [ ] at least one summary has reached the intended delivery sink

## Delivery-Mode Decision Guide

| Mode | Use when | Tradeoff |
|------|----------|----------|
| `incoming_webhook` | you only need simple posting into Teams | simplest setup, less control |
| `graph` | you need channel or chat posting through Graph | more control, more auth and target config |

## Operator Worksheet

Fill this out before rollout:

| Item | Value |
|------|-------|
| Public notification URL | |
| Graph tenant ID | |
| Graph client ID | |
| Webhook client state | |
| Transcript resource subscription | |
| Recording resource subscription | |
| Teams delivery mode | |
| Teams chat ID or team/channel | |
| Notion database ID | |
| Linear team ID | |
| Store path override, if any | |
| Owner for daily checks | |

## Change Review Worksheet

Use this before changing the deployment:

| Question | Answer |
|----------|--------|
| Are we changing the public webhook URL? | |
| Are we rotating Graph credentials? | |
| Are we changing Teams delivery mode? | |
| Are we moving to a new Teams chat or channel? | |
| Do subscriptions need to be recreated or renewed? | |
| Do we need a fresh end-to-end verification run? | |

## Related Docs

- [Teams Meetings setup](/docs/user-guide/messaging/teams-meetings)
- [Microsoft Teams bot setup](/docs/user-guide/messaging/teams)
