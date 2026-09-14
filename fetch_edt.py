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
from urllib.parse import urljoin

import requests
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

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


def extract_cas_error(html: str) -> str:
    """Busca el bloque de error tipico de CAS (div.errors o class='alert') en el HTML."""
    match = re.search(
        r'class="[^"]*(?:errors|alert|banner-danger)[^"]*"[^>]*>\s*(.*?)\s*<',
        html,
        re.IGNORECASE | re.DOTALL,
    )
    if match:
        return re.sub(r"\s+", " ", match.group(1)).strip()
    return "(no se encontro un mensaje de error especifico en el HTML)"


def submit_timetable_form(session: requests.Session, **params) -> str:
    """Simula elegir opciones en la pagina de EDT (estudiante, semanas, etc.),
    que dispara un POST y la pagina responde con el calendario real (con el
    criteria embebido) para esa seleccion."""
    resp = session.post(SERVICE_URL, data=params, verify=False)
    resp.raise_for_status()
    return resp.text


def cas_login(session: requests.Session, username: str, password: str) -> str:
    """Login CAS: pide el formulario, saca el token 'execution', manda user/pass.
    Devuelve el HTML de timetable_groups.php ya autenticado."""
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

    action_url = urljoin(resp.url, parser.form_action or "")

    login_resp = session.post(
        action_url,
        params={"service": SERVICE_URL},
        data=form_data,
        allow_redirects=False,  # no seguir el redirect todavia: primero validamos el login
    )

    if login_resp.status_code == 401:
        # CAS suele devolver 401 (en vez de redirigir) cuando el login falla.
        error_msg = extract_cas_error(login_resp.text)
        raise RuntimeError(
            f"CAS rechazo el login (401). Mensaje de la pagina: {error_msg!r}. "
            "Revisa ENIB_USER / ENIB_PASS en los Secrets del repo."
        )

    if login_resp.status_code not in (302, 303):
        raise RuntimeError(
            f"Login inesperado: status {login_resp.status_code} en vez de un redirect. "
            "Puede que la estructura de CAS haya cambiado."
        )

    redirect_url = login_resp.headers["Location"]

    # edt.enib.fr tiene un certificado SSL con la cadena incompleta (le falta el
    # intermedio) - los navegadores lo toleran, requests no. Como en este paso
    # ya no viaja la contrasena (solo el "ticket" de CAS), desactivamos la
    # verificacion SOLO para este dominio.
    follow_resp = session.get(redirect_url, verify=False)
    follow_resp.raise_for_status()
    return follow_resp.text


def extract_export_criteria(html: str) -> tuple[str, str]:
    """Busca los campos ocultos 'criteria' y 'weekCriteria' en la pagina de
    seleccion de grupos, que trae los valores frescos para la sesion actual."""
    parser = HiddenInputParser()
    parser.feed(html)
    criteria = parser.fields.get("criteria")
    week_criteria = parser.fields.get("weekCriteria")
    if not criteria:
        raise RuntimeError(
            "No encontre un campo 'criteria' en timetable_groups.php. "
            "Puede que la pagina no traiga un grupo pre-seleccionado, o que "
            "haya que elegirlo con otro parametro. Ver DEBUG en el log."
        )
    return criteria, week_criteria or ""


def fetch_ics(session: requests.Session, criteria: str, week_criteria: str) -> bytes:
    resp = session.post(
        VCAL_URL,
        data={"criteria": criteria, "weekCriteria": week_criteria},
        verify=False,  # mismo problema de certificado incompleto que en cas_login
    )
    print(
        f"DEBUG fetch_ics: status={resp.status_code} "
        f"content-type={resp.headers.get('Content-Type')} "
        f"len={len(resp.content)}",
        file=sys.stderr,
    )
    resp.raise_for_status()
    return resp.content


def clean_ics(raw: bytes) -> str:
    """Replica la limpieza que hicimos a mano: sacar wrapper HTML, decodificar
    windows-1252, agregar VERSION/PRODID, y TZID=Europe/Paris en cada evento."""
    text = raw.decode("windows-1252")

    match = re.search(r"BEGIN:VCALENDAR.*END:VCALENDAR", text, re.DOTALL)
    if not match:
        print("--- DEBUG: respuesta recibida (primeros 1000 caracteres) ---", file=sys.stderr)
        print(text[:1000], file=sys.stderr)
        print("--- FIN DEBUG ---", file=sys.stderr)
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

    groups_html = cas_login(session, username, password)

    # Buscar a Jero por nombre (student_id) en vez de elegir un grupo del
    # dropdown, y pedir el rango de semanas S37 -> S02.
    STUDENT_ID = "5785118"
    FROM_WEEK_CUSTOM_ID = os.environ.get("ENIB_FROM_WEEK_ID")  # TODO: semana 37
    TO_WEEK_CUSTOM_ID = os.environ.get("ENIB_TO_WEEK_ID")      # TODO: semana 02

    if FROM_WEEK_CUSTOM_ID and TO_WEEK_CUSTOM_ID:
        groups_html = submit_timetable_form(
            session,
            student_grouping_id="0",
            student_id=STUDENT_ID,
            from_week_custom_id=FROM_WEEK_CUSTOM_ID,
            to_week_custom_id=TO_WEEK_CUSTOM_ID,
        )
        print(
            f"DEBUG: estudiante {STUDENT_ID} seleccionado, semanas "
            f"{FROM_WEEK_CUSTOM_ID} -> {TO_WEEK_CUSTOM_ID}",
            file=sys.stderr,
        )
    else:
        print(
            "DEBUG: faltan ENIB_FROM_WEEK_ID / ENIB_TO_WEEK_ID, no se selecciona "
            "estudiante ni rango de semanas todavia",
            file=sys.stderr,
        )

    try:
        criteria, week_criteria = extract_export_criteria(groups_html)
        print("DEBUG: usando criteria extraido dinamicamente de la sesion actual", file=sys.stderr)
    except RuntimeError as e:
        print(f"DEBUG: fallo la extraccion dinamica ({e}); uso el criteria fijo de respaldo", file=sys.stderr)
        print(f"--- DEBUG: HTML total tiene {len(groups_html)} caracteres ---", file=sys.stderr)

        # Buscar donde aparece 'criteria' en cualquier parte de la pagina
        # (probablemente dentro de un <script>, no en un <input hidden>)
        occurrences = [m.start() for m in re.finditer("criteria", groups_html, re.IGNORECASE)]
        print(f"--- DEBUG: 'criteria' aparece {len(occurrences)} veces ---", file=sys.stderr)
        for i, pos in enumerate(occurrences[:6]):
            start = max(0, pos - 150)
            end = min(len(groups_html), pos + 250)
            print(f"--- ocurrencia {i} (pos {pos}) ---", file=sys.stderr)
            print(groups_html[start:end], file=sys.stderr)

        # Tambien buscar los <form> presentes en la pagina
        forms = re.findall(r"<form[^>]*>", groups_html, re.IGNORECASE)
        print(f"--- DEBUG: {len(forms)} <form> encontrados ---", file=sys.stderr)
        for f in forms:
            print(f, file=sys.stderr)

        # Volcar el contenido completo de selectYearForm y selectGroupForm
        for form_id in ("selectYearForm", "selectGroupForm"):
            m = re.search(
                rf'<form[^>]*id="{form_id}"[^>]*>(.*?)</form>',
                groups_html,
                re.IGNORECASE | re.DOTALL,
            )
            print(f"--- DEBUG: contenido de {form_id} ---", file=sys.stderr)
            if m:
                print(m.group(1)[:3000], file=sys.stderr)
            else:
                print("(no encontrado con regex)", file=sys.stderr)

        print("--- FIN DEBUG ---", file=sys.stderr)
        criteria, week_criteria = CRITERIA, WEEK_CRITERIA

    raw = fetch_ics(session, criteria, week_criteria)
    cleaned = clean_ics(raw)

    with open(out_path, "w", encoding="utf-8") as f:
        f.write(cleaned)

    n_events = cleaned.count("BEGIN:VEVENT")
    print(f"OK: {n_events} eventos guardados en {out_path}")


if __name__ == "__main__":
    main()
