# Sincronizacion automatica del EDT de ENIB

Este repo descarga tu emploi du temps de https://edt.enib.fr todos los dias
y lo deja como un .ics valido y limpio, listo para suscribir en Google Calendar.

## Setup (una sola vez)

1. Cread un repo nuevo en GitHub (puede ser privado) y subi estos archivos
   (`fetch_edt.py` y la carpeta `.github/workflows/`).

2. En el repo: Settings -> Secrets and variables -> Actions -> "New repository secret".
   Cread dos secrets:
   - `ENIB_USER` -> tu usuario de ENIB/CAS
   - `ENIB_PASS` -> tu contrasena de ENIB/CAS

   (Nunca van escritos en el codigo, quedan encriptados por GitHub)

3. Anda a la pestaña "Actions" del repo, elegi el workflow
   "Actualizar horario ENIB" y apreta "Run workflow" para probarlo manualmente
   una vez. Si todo sale bien, va a aparecer un commit nuevo con
   `timetable_enib.ics` actualizado.

4. Una vez que el archivo este en el repo, la URL para pegar en Google
   Calendar (Configuracion -> Agregar calendario -> Desde URL) es:

   https://raw.githubusercontent.com/TU-USUARIO/TU-REPO/main/timetable_enib.ics

   (si el repo es privado, esta URL no va a funcionar publicamente -
   ver seccion "Repo privado" abajo)

## Repo privado

Si el repo es privado, `raw.githubusercontent.com` va a pedir autenticacion
y Google Calendar no va a poder leerlo. Opciones:

- Hacer el repo publico (el .ics solo tiene horarios/aulas, sin datos
  sensibles reales mas alla de nombres de profesores, asi que para la
  mayoria esto es aceptable).
- O usar GitHub Pages para publicar solo el .ics de forma publica
  aunque el codigo del repo quede privado.

## Que hace el script

1. Login en https://cas.enib.fr (protocolo CAS: pide el formulario,
   saca el token `execution`, manda usuario/contrasena).
2. Con la sesion ya autenticada, pide el .ics a
   https://edt.enib.fr/timetable_vcal.php con el filtro de tus grupos
   (guardado en `CRITERIA` dentro del script).
3. Limpia el archivo:
   - saca el wrapper HTML que agrega el navegador
   - decodifica los acentos (venian en windows-1252)
   - agrega `VERSION:2.0` y `PRODID` (obligatorios, el original no los tenia)
   - agrega `TZID=Europe/Paris` a cada evento

## Si en algun momento deja de funcionar

Lo mas probable es que haya cambiado el `CRITERIA` (el filtro de tus
materias) si te cambian de grupo/año. Para renovarlo:

1. Entra a https://edt.enib.fr con DevTools abierto (F12 -> Network).
2. Exporta tu horario como la primera vez.
3. Copia el valor exacto del campo `criteria` del Payload de la
   request a `timetable_vcal.php` (click derecho -> Copy value).
4. Reemplaza la variable `CRITERIA` en `fetch_edt.py`.
