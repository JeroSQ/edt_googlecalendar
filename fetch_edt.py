#!/usr/bin/env python3
"""
Downloads a student's timetable from ENIB's EDT system (edt.enib.fr) via CAS
login, and cleans up the resulting .ics file so it's valid for calendar apps
(Google Calendar, Apple Calendar, etc.).

Required environment variables:
    ENIB_USER            Your ENIB / CAS username
    ENIB_PASS            Your ENIB / CAS password
    ENIB_STUDENT_NAME     A search string matching your name as it appears in
                          the student list (e.g. your last name). Matched
                          case-insensitively against each option's text.

Optional:
    ENIB_FROM_WEEK       Start week code, format YYWW (e.g. 2637 = week 37
                          of 2026). Defaults to the current week.
    ENIB_TO_WEEK         End week code, same format. Defaults to
                          ENIB_WEEKS_AHEAD weeks after ENIB_FROM_WEEK.
    ENIB_WEEKS_AHEAD      How many weeks ahead to fetch when ENIB_TO_WEEK is
                          not set. Default: 20.

See README.md for instructions on finding your STUDENT_ID / GROUP_ID.

Usage:
    ENIB_USER=... ENIB_PASS=... ENIB_STUDENT_ID=... python3 fetch_edt.py out.ics
"""

import os
import re
import sys
from datetime import date, timedelta
from html.parser import HTMLParser
from urllib.parse import urljoin

import requests
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

CAS_LOGIN_URL = "https://cas.enib.fr/login"
SERVICE_URL = "https://edt.enib.fr/timetable_groups.php"
VCAL_URL = "https://edt.enib.fr/timetable_vcal.php"

DEFAULT_WEEKS_AHEAD = 20


class HiddenInputParser(HTMLParser):
    """Collects every <input> field's name/value found in a page, and the
    action attribute of the CAS login form (id='fm1')."""

    def __init__(self):
        super().__init__()
        self.fields = {}
        self.form_action = None

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "form" and attrs.get("id") == "fm1":
            self.form_action = attrs.get("action")
        if tag == "input":
            name = attrs.get("name")
            if name:
                self.fields[name] = attrs.get("value", "")


def week_code(d: date) -> str:
    """Converts a date into ENIB's week code format: 2-digit ISO year +
    2-digit ISO week number (e.g. 2026 week 37 -> '2637')."""
    iso_year, iso_week, _ = d.isocalendar()
    return f"{iso_year % 100:02d}{iso_week:02d}"


def extract_cas_error(html: str) -> str:
    """Looks for CAS's typical error block (class 'errors'/'alert') in the HTML."""
    match = re.search(
        r'class="[^"]*(?:errors|alert|banner-danger)[^"]*"[^>]*>\s*(.*?)\s*<',
        html,
        re.IGNORECASE | re.DOTALL,
    )
    if match:
        return re.sub(r"\s+", " ", match.group(1)).strip()
    return "(no specific error message found in the HTML)"


def cas_login(session: requests.Session, username: str, password: str) -> str:
    """Logs in via CAS: fetches the login form, grabs the 'execution' token,
    submits username/password. Returns the HTML of timetable_groups.php,
    already authenticated."""
    resp = session.get(CAS_LOGIN_URL, params={"service": SERVICE_URL})
    resp.raise_for_status()

    parser = HiddenInputParser()
    parser.feed(resp.text)

    if "execution" not in parser.fields:
        raise RuntimeError(
            "Could not find the 'execution' field on the CAS login page. "
            "The login form's structure may have changed."
        )

    form_data = dict(parser.fields)
    form_data["username"] = username
    form_data["password"] = password
    form_data.setdefault("_eventId", "submit")

    action_url = urljoin(resp.url, parser.form_action or "")

    login_resp = session.post(
        action_url,
        params={"service": SERVICE_URL},
        data=form_data,
        allow_redirects=False,  # don't follow yet: check the login result first
    )

    if login_resp.status_code == 401:
        # CAS returns 401 (instead of redirecting) when login fails.
        error_msg = extract_cas_error(login_resp.text)
        raise RuntimeError(
            f"CAS rejected the login (401). Page message: {error_msg!r}. "
            "Check ENIB_USER / ENIB_PASS."
        )

    if login_resp.status_code not in (302, 303):
        raise RuntimeError(
            f"Unexpected login response: status {login_resp.status_code} "
            "instead of a redirect. CAS's structure may have changed."
        )

    redirect_url = login_resp.headers["Location"]

    # edt.enib.fr serves an SSL certificate with an incomplete chain (missing
    # intermediate) - browsers tolerate this, requests doesn't. Since no
    # credentials travel in this step (only the CAS ticket), we disable
    # verification for this domain only.
    follow_resp = session.get(redirect_url, verify=False)
    follow_resp.raise_for_status()
    return follow_resp.text


def submit_timetable_form(session: requests.Session, **params) -> str:
    """Simulates picking options on the EDT page (student, group, week range),
    which the site handles via a POST back to itself, returning the page with
    the matching calendar/export data embedded."""
    resp = session.post(SERVICE_URL, data=params, verify=False)
    resp.raise_for_status()
    return resp.text


def extract_student_id(html: str, name_query: str) -> str:
    """Finds the <select id="student_id"> dropdown embedded in the timetable
    page and returns the numeric value of the option whose text matches
    name_query (case-insensitive substring match)."""
    select_match = re.search(
        r'<select[^>]*id="student_id"[^>]*>(.*?)</select>',
        html,
        re.IGNORECASE | re.DOTALL,
    )
    if not select_match:
        raise RuntimeError("Could not find the student_id dropdown on the page.")

    options = re.findall(
        r'<option[^>]*value="(\d+)"[^>]*>([^<]*)</option>',
        select_match.group(1),
    )
    query = name_query.strip().lower()
    matches = [(value, text.strip()) for value, text in options if query in text.lower()]

    if not matches:
        raise RuntimeError(f"No student matched ENIB_STUDENT_NAME={name_query!r}.")
    if len(matches) > 1:
        preview = ", ".join(f"{text} ({value})" for value, text in matches[:10])
        raise RuntimeError(
            f"ENIB_STUDENT_NAME={name_query!r} matched multiple students: {preview}. "
            "Use a more specific value (e.g. full last name)."
        )
    return matches[0][0]


def extract_export_criteria(html: str) -> tuple[str, str]:
    """Finds the hidden 'criteria' / 'weekCriteria' fields on the timetable
    page, which hold the fresh export filter for the current selection."""
    parser = HiddenInputParser()
    parser.feed(html)
    criteria = parser.fields.get("criteria")
    week_criteria = parser.fields.get("weekCriteria")
    if not criteria:
        raise RuntimeError(
            "Could not find a 'criteria' field on the timetable page. "
            "Double-check ENIB_STUDENT_ID / ENIB_GROUP_ID and the week codes."
        )
    return criteria, week_criteria or ""


def fetch_ics(session: requests.Session, criteria: str, week_criteria: str) -> bytes:
    resp = session.post(
        VCAL_URL,
        data={"criteria": criteria, "weekCriteria": week_criteria},
        verify=False,  # same incomplete-certificate issue as above
    )
    resp.raise_for_status()
    return resp.content


def clean_ics(raw: bytes) -> str:
    """Strips the HTML wrapper the server adds, decodes the windows-1252
    text, adds the mandatory VERSION/PRODID header lines, and pins every
    event's time to Europe/Paris."""
    text = raw.decode("windows-1252")

    match = re.search(r"BEGIN:VCALENDAR.*END:VCALENDAR", text, re.DOTALL)
    if not match:
        raise RuntimeError(
            "The server's response did not contain a VCALENDAR block. "
            f"First 300 characters of the response: {text[:300]!r}"
        )
    body = match.group(0)

    lines = body.split("\n")
    assert lines[0].strip() == "BEGIN:VCALENDAR"

    header_extra = ["VERSION:2.0", "PRODID:-//ENIB//Timetable//EN", "CALSCALE:GREGORIAN"]
    lines = [lines[0]] + header_extra + lines[1:]

    fixed_lines = []
    for line in lines:
        line = line.rstrip("\r")
        if line.startswith("DTSTART:") or line.startswith("DTEND:"):
            key, val = line.split(":", 1)
            line = f"{key};TZID=Europe/Paris:{val}"
        fixed_lines.append(line)

    return "\r\n".join(fixed_lines) + "\r\n"


def main():
    username = os.environ.get("ENIB_USER")
    password = os.environ.get("ENIB_PASS")
    student_name = os.environ.get("ENIB_STUDENT_NAME")
    out_path = sys.argv[1] if len(sys.argv) > 1 else "timetable.ics"

    if not username or not password:
        print("Missing ENIB_USER / ENIB_PASS environment variables.", file=sys.stderr)
        sys.exit(1)

    if not student_name:
        print(
            "Missing ENIB_STUDENT_NAME environment variable. See README.md.",
            file=sys.stderr,
        )
        sys.exit(1)

    today = date.today()
    weeks_ahead = int(os.environ.get("ENIB_WEEKS_AHEAD") or DEFAULT_WEEKS_AHEAD)
    from_week = os.environ.get("ENIB_FROM_WEEK") or week_code(today)
    to_week = os.environ.get("ENIB_TO_WEEK") or week_code(today + timedelta(weeks=weeks_ahead))

    session = requests.Session()
    session.headers.update({"User-Agent": "Mozilla/5.0 (edt-sync-script)"})

    groups_html = cas_login(session, username, password)
    student_id = extract_student_id(groups_html, student_name)

    timetable_html = submit_timetable_form(
        session,
        student_grouping_id="0",
        student_id=student_id,
        from_week_custom_id=from_week,
        to_week_custom_id=to_week,
    )
    criteria, week_criteria = extract_export_criteria(timetable_html)

    raw = fetch_ics(session, criteria, week_criteria)
    cleaned = clean_ics(raw)

    with open(out_path, "w", encoding="utf-8") as f:
        f.write(cleaned)

    n_events = cleaned.count("BEGIN:VEVENT")
    print(f"Saved {n_events} events to {out_path}")


if __name__ == "__main__":
    main()
