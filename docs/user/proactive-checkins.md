# Proactive check-ins

An optional morning brief, plus a few quiet check-ins during the day. A check-in
messages you only when something needs you today. Otherwise it says nothing.
It is **on by default**. The first time the gateway starts, Clover creates the
two jobs. `/proactive checkin off` removes them and keeps them off.

## Turn it on (if you turned it off)

```
/proactive checkin on
```

This creates two ordinary scheduled jobs (see them with `/cron`):

- **Morning brief** at your brief time. A short summary of calendar, mail, tasks
  and open work. It uses whichever of those you have connected.
- **Check-in** every few hours, only inside your active hours. Before each run a
  small script (no model call, no cost) checks the rules below. If they say no,
  nothing runs.

Clover never sends or changes anything for you. It offers drafts and asks.

## Settings

| Command | What it does |
| --- | --- |
| `/proactive checkin on` / `off` | Create or delete both jobs |
| `/proactive checkin status` | Interval, next run, sent today, warnings |
| `/proactive checkin brief 07:30` / `brief off` | Brief time, or no brief |
| `/proactive checkin hours 9-20` | No check-ins outside these hours (overnight like `22-6` works) |
| `/proactive checkin every 3` | Hours between checks (1-24) |
| `/proactive checkin max 2` | At most this many check-ins a day (brief not counted) |
| `/proactive checkin pause 24h` / `resume` | Pause without deleting the jobs |

The same settings live in `config.yaml` under `proactive.checkin`
(`morning_brief`, `checks_every_hours`, `active_hours`, `max_per_day`,
`min_gap_minutes`, `timezone`, `deliver`). Change them with the commands above
or `clover config set`.

## Rules a check-in must pass

It does not run if check-ins are off or paused, it is outside active hours
(end time is exclusive), the daily limit is reached, or the last check-in was
less than `min_gap_minutes` (default 90) ago. "Today" uses your timezone.
Only messages that were really delivered count. A check that finds nothing
replies `[SILENT]` and counts for nothing.

## How they look, and how to stop them

Check-ins arrive as ordinary messages from your agent, not as scheduled-job
cards: no header, no job id, no footer. Your reply continues the conversation
(for example "draft the Baptiste one"), because each check-in is added to that
chat's history. To stop them, send `/proactive checkin off`, or `pause 24h`
for a break.

## Things to know

- Delivery goes to your home channel. Set it with `/sethome`. `status` warns if
  it is missing.
- The gateway must be running for scheduled jobs to fire. `status` warns if not.
- Checks run every few hours, so something urgent can wait until the next check.
- Admin-locked profiles cannot change these settings.
- If you already have a morning-brief or important-mail job, `on` tells you so
  you can remove one and avoid duplicates.

See also [Proactive workstreams](proactive-workstreams.md).
