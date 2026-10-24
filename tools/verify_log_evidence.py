"""Installed-package entry point; run after installing guard's wheel."""
from guard.log_evidence import main

if __name__ == "__main__":
    raise SystemExit(main())
