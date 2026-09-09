#!/usr/bin/env python3
"""
Descarga el emploi du temps de ENIB (edt.enib.fr) haciendo login vía CAS,
y limpia el .ics resultante para que sea válido (Google Calendar, etc.).

Credenciales via variables de entorno:
    ENIB_USER
    ENIB_PASS

Uso:
    ENIB_USER=... ENIB_PASS=... python3 fetch_edt.py salida.ics
"""

import os
import re
import sys
from html.parser import HTMLParser

import requests

CAS_LOGIN_URL = "https://cas.enib.fr/login"
SERVICE_URL = "https://edt.enib.fr/timetable_groups.php"
VCAL_URL = "https://edt.enib.fr/timetable_vcal.php"

# Filtro fijo obtenido desde el navegador (no depende de la semana elegida)
CRITERIA = (
    "c2Vzc2lvbi5kYXRhX2lkPTc2NTUgQU5EIG1haW5fcGFydGljaXBhbnQucGFydGljaXBhbnRfaWQg"
    "SU4gKDM0NTQwODU2LDM0NTQxNTY1LDM0NTQwOTc0LDM0NTQwOTczLDM0NTQxMDIzLDM0NTQxMDIy"
    "LDM0NTQwOTg2LDM0NTQwOTg0LDM0NTQwOTkwLDM0NTQwOTg3LDM0NTQwOTk1LDM0NTQwOTkyLDM0"
    "NTQxMDAyLDM0NTQwOTk5LDM0NTQxMDA3LDM0NTQxMDA0LDM0NTQxMDEyLDM0NTQxMDA5LDM0NTQx"
    "MDI1LDM0NTQxMDE4LDM0NTQxMDE0KQ=="
)
WEEK_CRITERIA = " 1 "


class HiddenInputParser(HTMLParser):
    """Extrae todos los <input type=hidden> de un formulario CAS."""

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


def cas_login(session: requests.Session, username: str, password: str) -> None:
    """Login CAS: pide el formulario, saca el token 'execution', manda user/pass."""
    resp = session.get(CAS_LOGIN_URL, params={"service": SERVICE_URL})
    resp.raise_for_status()

    parser = HiddenInputParser()
    parser.feed(resp.text)

    if "execution" not in parser.fields:
        raise RuntimeError(
            "No encontre el campo 'execution' en la pagina de login. "
            "Puede que la estructura del formulario CAS haya cambiado."
        )

    form_data = dict(parser.fields)
    form_data["username"] = username
    form_data["password"] = password
    form_data.setdefault("_eventId", "submit")

    action_url = parser.form_action or CAS_LOGIN_URL
    if action_url.startswith("/"):
        action_url = "https://cas.enib.fr" + action_url
    elif action_url.startswith("?"):
        action_url = CAS_LOGIN_URL + action_url

    login_resp = session.post(
        action_url,
        params={"service": SERVICE_URL},
        data=form_data,
        allow_redirects=True,
    )
    login_resp.raise_for_status()

    if "cas.enib.fr" in login_resp.url:
        raise RuntimeError(
            "El login parece haber fallado (seguimos en cas.enib.fr). "
            "Revisa usuario/contrasena."
        )


def fetch_ics(session: requests.Session) -> bytes:
    resp = session.post(
        VCAL_URL,
        data={"criteria": CRITERIA, "weekCriteria": WEEK_CRITERIA},
    )
    resp.raise_for_status()
    return resp.content


def clean_ics(raw: bytes) -> str:
    """Replica la limpieza que hicimos a mano: sacar wrapper HTML, decodificar
    windows-1252, agregar VERSION/PRODID, y TZID=Europe/Paris en cada evento."""
    text = raw.decode("windows-1252")

    match = re.search(r"BEGIN:VCALENDAR.*END:VCALENDAR", text, re.DOTALL)
    if not match:
        raise RuntimeError("La respuesta no contiene un bloque VCALENDAR valido.")
    body = match.group(0)

    lines = body.split("\n")
    assert lines[0].strip() == "BEGIN:VCALENDAR"

    header_extra = ["VERSION:2.0", "PRODID:-//ENIB//Timetable//FR", "CALSCALE:GREGORIAN"]
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
    out_path = sys.argv[1] if len(sys.argv) > 1 else "timetable_enib.ics"

    if not username or not password:
        print("Faltan ENIB_USER / ENIB_PASS como variables de entorno.", file=sys.stderr)
        sys.exit(1)

    session = requests.Session()
    session.headers.update({"User-Agent": "Mozilla/5.0 (edt-sync-script)"})

    cas_login(session, username, password)
    raw = fetch_ics(session)
    cleaned = clean_ics(raw)

    with open(out_path, "w", encoding="utf-8") as f:
        f.write(cleaned)

    n_events = cleaned.count("BEGIN:VEVENT")
    print(f"OK: {n_events} eventos guardados en {out_path}")


if __name__ == "__main__":
    main()
