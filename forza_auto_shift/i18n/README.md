# Qt i18n Files

This folder uses Qt translation files (`.ts` -> `.qm`) for PySide6 built-in i18n.

## Build translations

Run from the workspace root:

```powershell
venv/Scripts/pyside6-lrelease.exe forza_auto_shift/i18n/forza_auto_shift_es.ts -qm forza_auto_shift/i18n/forza_auto_shift_es.qm
```

You can generate or update TS files with:

```powershell
venv/Scripts/pyside6-lupdate.exe -tr-function-alias tr+=_t forza_auto_shift/gui_app.py -ts forza_auto_shift/i18n/forza_auto_shift_es.ts
```

The app loads `forza_auto_shift_<lang>.qm` from this folder on startup.
