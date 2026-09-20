"""faa2xp: back-port the FAA CIFP into an X-Plane 12 install's ``Custom Data/``.

Offline tool (never imported by the trainer's main loop). Pipeline::

    FAACIFP18 --extract--> records --format--> earth_*.dat rows
    rows + X-Plane's default data --merge--> staged earth_*.dat
    staged files + FAACIFP18 --install--> <X-Plane>/Custom Data/  (+ manifest, restore)
"""
