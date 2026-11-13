"""PyInstaller entry point.

PyInstaller runs its entry script as ``__main__``, so it can't be
``ci_cli/main.py`` itself (that uses package-relative imports).
"""

from ci_cli.main import main

if __name__ == "__main__":
    main()
