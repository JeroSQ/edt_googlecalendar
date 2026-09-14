# ENIB Timetable → Google Calendar Sync

Automatically fetches your class timetable from ENIB's EDT system
(`edt.enib.fr`) every day and publishes it as a clean `.ics` file you can
subscribe to from Google Calendar (or any calendar app that supports
"subscribe from URL").

## How it works

A GitHub Actions workflow runs once a day, logs into `edt.enib.fr` on your
behalf (via CAS), downloads your timetable, cleans it up (the raw export has
a few quirks that break most calendar apps), and commits the result back to
this repo as `timetable.ics`. Your calendar app then reads that file's raw
URL and refreshes itself periodically — no manual exporting ever again.

## Setup

### 1. Fork this repo

Click "Fork" at the top of the page. You can keep it public or private (see
[Making the file public](#making-the-file-public) below for the tradeoffs).

### 2. Add your credentials as repo secrets

Go to **Settings → Secrets and variables → Actions → New repository secret**
and add:

| Secret | Value |
|---|---|
| `ENIB_USER` | Your ENIB/CAS username |
| `ENIB_PASS` | Your ENIB/CAS password |
| `ENIB_STUDENT_NAME` | A search string matching your name (e.g. your last name) |

`ENIB_STUDENT_NAME` is matched case-insensitively against the student list
ENIB's own site uses internally (e.g. `"SQUARTINI"` matches `"SQUARTINI
Jeronimo"`). This is resolved fresh every time the workflow runs, so it
doesn't break when ENIB's internal IDs change — no DevTools digging
required, just use a name specific enough to match only you. If it matches
more than one student, the workflow will fail with a list of the matches so
you can make it more specific (e.g. full last name instead of just part of
it).

Optional secrets (only needed if the defaults don't fit your case):

| Secret | Purpose |
|---|---|
| `ENIB_FROM_WEEK` | Start week, format `YYWW` (e.g. `2637` = week 37 of 2026). Defaults to the current week. |
| `ENIB_TO_WEEK` | End week, same format. Defaults to `ENIB_WEEKS_AHEAD` weeks after the start. |
| `ENIB_WEEKS_AHEAD` | How many weeks ahead to fetch when `ENIB_TO_WEEK` isn't set. Default: `20`. |

### 3. Allow the workflow to push commits

Go to **Settings → Actions → General → Workflow permissions** and select
**"Read and write permissions"**. (The workflow file also requests this
explicitly, but some accounts require the toggle too.)

### 4. Run it once manually

Go to the **Actions** tab → "Update ENIB timetable" → **Run workflow**. If it
succeeds, you'll see a new commit adding `timetable.ics`.

### 5. Subscribe from Google Calendar

Open the `timetable.ics` file in your repo, click **"Raw"**, and copy that
URL — it looks like:

```
https://raw.githubusercontent.com/YOUR-USERNAME/YOUR-REPO/main/timetable.ics
```

In Google Calendar: **Settings → Add calendar → From URL**, paste it, done.
Add a Google Calendar widget to your phone's home screen and it'll stay in
sync automatically (Google re-checks subscribed URLs roughly every 8–24
hours; there's no way to force a faster refresh on the free tier).

## Making the file public

`raw.githubusercontent.com` only serves files from **public** repos (or
private ones you're authenticated into) — Google Calendar can't authenticate,
so it needs the repo to be public. The `.ics` only contains class schedules,
rooms, and teacher names, so for most people this is an acceptable tradeoff.
If you'd rather keep the repo private, GitHub Pages can publish just the
`.ics` file publicly while the rest of the repo stays private — but Pages
itself requires GitHub Pro on private repos, so on a free account your only
option is a public repo.

## Troubleshooting

- **Workflow fails with a 401 during login** — wrong `ENIB_USER`/`ENIB_PASS`,
  or a stray space/newline got pasted into the secret's value.
- **"No student matched ENIB_STUDENT_NAME"** or **"matched multiple
  students"** — adjust the value to match your name more precisely (the
  error message lists the closest matches it found).
- **"Could not find a 'criteria' field"** — usually means the student/week
  selection didn't go through as expected; check the previous error first.
- **SSL certificate errors** — `edt.enib.fr` serves an incomplete certificate
  chain; the script already works around this for that domain specifically.
  If you see this error elsewhere, something upstream may have changed.
- **GitHub Actions billing lock** — some new GitHub accounts need a payment
  method on file (even for free-tier Actions minutes) before workflows can
  run. See **Settings → Billing**.
- If ENIB changes their site's structure (it's happened before while
  building this), the workflow may start failing. Check the **Actions** tab
  occasionally.
