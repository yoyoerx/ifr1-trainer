"""`python -m datasrc ...` -> the FAA updater CLI."""

from .faa import main

raise SystemExit(main())
