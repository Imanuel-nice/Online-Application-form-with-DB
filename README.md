# DMI Online Application Form

## Run locally

Python 3 is required; the backend uses only Python's standard library.

On Windows, open a terminal in this folder and run:

```powershell
py -3 server.py
```

Then open <http://127.0.0.1:8000>. Submit the form while the server is running.
The server saves each application in `data/applications.sqlite3` and returns a
unique reference number after a successful save. The optional passport photo is
stored with the application.

The JSON API accepts `POST /api/applications`; `GET /api/health` can be used to
check that the local server is running.

## Data handling

Applications contain sensitive personal information and are stored unencrypted
on the local machine. The backend binds to `127.0.0.1` and is intended for local
development, not direct public deployment. A production deployment needs
authenticated access, encrypted storage and backups, and appropriate privacy
controls.
