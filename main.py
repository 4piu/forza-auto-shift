"""PyInstaller launcher for the GUI app.

Using an absolute import avoids relative-import issues when frozen.
"""

from forza_auto_shift.gui_app import main


if __name__ == "__main__":
    main()
