# API reference

Base URL: `http://localhost:8000/api`. Every endpoint except `/login`,
`/register`, `/password-strength`, and `/health` requires the session token
returned by `/login` in an `x-session` header.

Roles: `Administrator`, `SecurityOperator`, `User`. An endpoint with no
**Roles** column entry just requires *any* active session.

## Auth

| Method | Path | Roles | Body | Notes |
|---|---|---|---|---|
| POST | `/password-strength` | — | `password` | Live scoring while typing; returns `{level, color, score, checks}` |
| POST | `/register` | — | `username, full_name, password` | Rejects weak passwords; new account starts `pending` |
| POST | `/login` | — | `username, password` | 403 if account is `pending`/`rejected`; returns session token |
| POST | `/logout` | any | — | Deletes the session row (`x-session` header) |
| GET | `/me` | any | — | Current user's `username, role, full_name` |

## Admin — users

| Method | Path | Roles | Body |
|---|---|---|---|
| GET | `/admin/users` | Administrator | — |
| POST | `/admin/users/{uid}/approve` | Administrator | `action=approve\|reject, role` |
| PATCH | `/admin/users/{uid}` | Administrator | `full_name?, role?` — edits any active/pending user |
| DELETE | `/admin/users/{uid}` | Administrator | Blocks self-deletion and deleting the last active Administrator |

## Projects & upload

| Method | Path | Roles | Notes |
|---|---|---|---|
| POST | `/projects` | any | `multipart/form-data` with `file` (mp4/avi/mov/mkv, ≤500MB). Returns immediately; pipeline runs in the background |
| GET | `/projects` | any | List, newest first, with live segment counts |
| GET | `/projects/{pid}` | any | Project + all its segments |
| PATCH | `/projects/{pid}` | Administrator, SecurityOperator | `name` — rename the project (defaults to the uploaded filename) |
| DELETE | `/projects/{pid}` | Administrator, SecurityOperator | Cascades segments/events/alerts and deletes archived files |
| DELETE | `/segments/{sid}` | Administrator, SecurityOperator | Deletes one segment + its files |
| POST | `/segments/{sid}/tier` | Administrator, SecurityOperator | `tier=HIGH\|MEDIUM\|LOW`, sets `tier_manual=1` |

## Search & playback

| Method | Path | Roles | Query params |
|---|---|---|---|
| GET | `/search` | any | `start, end` (ISO), `objects, actions` (substring), `tier`, `min_ssig, max_ssig`, `page, page_size` |
| GET | `/segments/{sid}` | any | Full metadata for one segment |
| GET | `/thumb/{sid}` | none¹ | JPEG thumbnail |
| GET | `/playback/{sid}` | none¹ | Video (or keyframe JPEG for LOW-tier segments with no video) |

¹ These two are served without the `require()` auth check — fine for a local
demo, but see [DEPLOYMENT.md](DEPLOYMENT.md) before exposing this publicly.

## Analytics

| Method | Path | Roles | Returns |
|---|---|---|---|
| GET | `/analytics` | any | Storage report, project/tier/threat breakdowns, device |
| GET | `/status` | any | Active/completed project counts — meant for 2s polling |
| GET | `/storage` | any | Same shape as `analytics.storage` |
| GET | `/events` | any | Last 200 significant events |
| GET | `/recommendations` | any | Last 50 auto-generated recommendations |

## Alerts

| Method | Path | Roles | Body |
|---|---|---|---|
| GET | `/alerts` | Administrator, SecurityOperator | Last 200, newest first |
| POST | `/alerts/{aid}/ack` | Administrator, SecurityOperator | — (acks with current user + timestamp) |
| DELETE | `/alerts/{aid}` | Administrator | Permanently removes an alert record |

## Configuration

| Method | Path | Roles | Body |
|---|---|---|---|
| GET | `/config` | Administrator | — |
| POST | `/config` | Administrator | JSON body of any config keys; rejects `threshold_low >= threshold_high` |

Config keys: `threshold_high`, `threshold_low`, `alert_threshold`,
`motion_sensitivity`, `object_conf_threshold`, `action_min_frames`,
`segment_seconds`, `context_rules` (JSON: `night_boost`, `weapon_boost`, `crowd_boost`).

## Logs

| Method | Path | Roles | Notes |
|---|---|---|---|
| GET | `/logs` | Administrator | `type, severity, limit` filters |
| GET | `/logs/stream` | any | Server-Sent Events, polls every 1s for ~10 minutes. `EventSource` can't send custom headers, so auth is passed as `?session=TOKEN` instead of `x-session` here — the only endpoint that accepts a token this way. Powers both the Logs page's live stream and the Projects page's animated pipeline tracker. |

## Misc

| Method | Path | Notes |
|---|---|---|
| GET | `/` | Serves `static/index.html` |
| GET | `/health` | `{ok: true, device: "mps"\|"cuda"\|"cpu"}` — no auth, used for container health checks |
