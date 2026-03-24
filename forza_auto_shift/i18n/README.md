# Qt i18n Files

This folder uses Qt translation files (`.ts` -> `.qm`) for PySide6 built-in i18n.

## Build translations

Run from the workspace root:

```powershell
venv/Scripts/pyside6-lrelease.exe forza_auto_shift/i18n/forza_auto_shift_es.ts -qm forza_auto_shift/i18n/forza_auto_shift_es.qm
venv/Scripts/pyside6-lrelease.exe forza_auto_shift/i18n/forza_auto_shift_fr.ts -qm forza_auto_shift/i18n/forza_auto_shift_fr.qm
venv/Scripts/pyside6-lrelease.exe forza_auto_shift/i18n/forza_auto_shift_de.ts -qm forza_auto_shift/i18n/forza_auto_shift_de.qm
venv/Scripts/pyside6-lrelease.exe forza_auto_shift/i18n/forza_auto_shift_zh_cn.ts -qm forza_auto_shift/i18n/forza_auto_shift_zh_cn.qm
venv/Scripts/pyside6-lrelease.exe forza_auto_shift/i18n/forza_auto_shift_zh_tw.ts -qm forza_auto_shift/i18n/forza_auto_shift_zh_tw.qm
venv/Scripts/pyside6-lrelease.exe forza_auto_shift/i18n/forza_auto_shift_ja_jp.ts -qm forza_auto_shift/i18n/forza_auto_shift_ja_jp.qm
```

You can generate or update TS files with:

```powershell
venv/Scripts/pyside6-lupdate.exe -tr-function-alias tr+=_t forza_auto_shift/gui_app.py -ts forza_auto_shift/i18n/forza_auto_shift_es.ts
venv/Scripts/pyside6-lupdate.exe -tr-function-alias tr+=_t forza_auto_shift/gui_app.py -ts forza_auto_shift/i18n/forza_auto_shift_fr.ts
venv/Scripts/pyside6-lupdate.exe -tr-function-alias tr+=_t forza_auto_shift/gui_app.py -ts forza_auto_shift/i18n/forza_auto_shift_de.ts
venv/Scripts/pyside6-lupdate.exe -tr-function-alias tr+=_t forza_auto_shift/gui_app.py -ts forza_auto_shift/i18n/forza_auto_shift_zh_cn.ts
venv/Scripts/pyside6-lupdate.exe -tr-function-alias tr+=_t forza_auto_shift/gui_app.py -ts forza_auto_shift/i18n/forza_auto_shift_zh_tw.ts
venv/Scripts/pyside6-lupdate.exe -tr-function-alias tr+=_t forza_auto_shift/gui_app.py -ts forza_auto_shift/i18n/forza_auto_shift_ja_jp.ts
```

The app loads `forza_auto_shift_<lang>.qm` from this folder on startup.
