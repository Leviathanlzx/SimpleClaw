---
name: cron
description: Schedule reminders and recurring tasks.
---

# Cron

Use the `cron` tool to schedule reminders or recurring tasks.

The target output channel is **automatically captured** from the current conversation context.
For example, if a user on Telegram asks "remind me every day at 9am", the reminder will be
delivered back to that Telegram chat — no manual channel specification needed.

## Parameters for `add`

Provide `message` + **exactly one** of:

| Parameter | Type | When to use |
|-----------|------|-------------|
| `every_seconds` | integer | Repeat every fixed number of seconds |
| `cron_expr` | string | Repeat on a clock-aligned schedule (daily, weekly, etc.) |
| `at` | string (ISO datetime) | Run once at a specific date and time |

## Time Expression Guide

| User says | Use | Value |
|-----------|-----|-------|
| every 20 minutes | `every_seconds` | 1200 |
| every hour | `every_seconds` | 3600 |
| every day at 8am | `cron_expr` | `"0 8 * * *"` |
| every weekday at 5pm | `cron_expr` | `"0 17 * * 1-5"` |
| every Monday at 9am | `cron_expr` | `"0 9 * * 1"` |
| tomorrow at 3pm | `at` | compute ISO datetime from current time |
| remind me at a specific time | `at` | compute ISO datetime from current time |

## Examples

```
cron(action="add", message="Time to take a break!", every_seconds=1200)

cron(action="add", message="Daily standup reminder", cron_expr="0 9 * * 1-5")

cron(action="add", message="Remind me about the meeting", at="2024-06-01T15:00:00")

cron(action="list")

cron(action="remove", job_id="abc123")
```
