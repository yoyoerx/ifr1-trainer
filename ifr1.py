"""Octavi IFR-1 USB HID interface.

The device is a plain HID gadget:

    VID / PID       0x04D8 / 0xE6D6
    Input report    ID 0x0B, pushed on any state change
    Output report   ID 0x0B, payload byte = AP-row LED bitmask

CANONICAL FRAME -- indexed from the report-id byte, which this module always
keeps (and synthesises if a hidapi backend stripped it):

    [0] 0x0B     report id
    [1] btn_a    DCT 0x10  MNU 0x20  CLR 0x40  ENT 0x80
    [2] btn_b    SWAP 0x01 KNOB 0x02 AP 0x40   HDG 0x80
    [3] btn_c    NAV 0x01  APR 0x02  ALT 0x04  VS 0x08
    [4] btn_d    further buttons -- masks unconfirmed (surfaced as UNKNOWN)
    [5] outer    outer-knob delta since last report, signed -128..127
    [6] inner    inner-knob delta since last report, signed
    [7] mode     rotary mode selector, 0..7

    mode: 0 COM1  1 COM2  2 NAV1  3 NAV2  4 FMS1  5 FMS2  6 AP  7 XPDR

These offsets are derived from a macOS/Linux FlyWithLua script and NOT yet
confirmed on Windows hardware. Run `python ifr1.py raw`, work every control, and
check the byte that moves against this table -- adjust `LAYOUT` if it differs.
"""

from __future__ import annotations

import sys
import time
from dataclasses import dataclass, field
from enum import IntEnum

try:
    import hid  # provided by the `hidapi` package (cython-hidapi)
except ImportError as exc:  # pragma: no cover - import guard
    raise SystemExit(
        "Cannot import 'hid'. Install the cython-hidapi build:  pip install -r "
        "requirements.txt\n"
        "Note: the 'hidapi' package imports as `hid`. The similarly named pure-python "
        "'hid' package needs a separate hidapi.dll/.so and is NOT what this expects."
    ) from exc

IFR1_VID = 0x04D8
IFR1_PID = 0xE6D6
REPORT_ID = 0x0B
READ_SIZE = 64  # generous; the real report is ~8 bytes
# a button held this long fires one synthetic long-press Event (CLR-hold ->
# Default NAV, COM SWAP-hold -> 121.5): Pilot's Guide sec.1.2 "press and hold".
# The device only pushes a report on a state CHANGE, so a sustained press with
# nothing else moving produces no further frames - `IFR1.poll()` is called
# every ~30 Hz loop tick regardless, so the hold is timed off the wall clock,
# not off new reports.
LONG_PRESS_S = 0.6


class Mode(IntEnum):
    COM1 = 0
    COM2 = 1
    NAV1 = 2
    NAV2 = 3
    FMS1 = 4
    FMS2 = 5
    AP = 6
    XPDR = 7

    @classmethod
    def from_raw(cls, v: int) -> "Mode":
        v &= 0x0F
        return cls(v) if 0 <= v <= 7 else cls.COM1


@dataclass(frozen=True)
class Layout:
    """Byte offsets into the canonical frame (report-id byte = index 0)."""

    outer: int = 5
    inner: int = 6
    mode: int = 7
    # button name -> (frame byte index, bitmask)
    buttons: dict[str, tuple[int, int]] = field(
        default_factory=lambda: {
            "DCT": (1, 0x10),
            "MNU": (1, 0x20),
            "CLR": (1, 0x40),
            "ENT": (1, 0x80),
            "SWAP": (2, 0x01),
            "KNOB": (2, 0x02),  # inner-knob push / CRSR
            "AP": (2, 0x40),
            "HDG": (2, 0x80),
            "NAV": (3, 0x01),
            "APR": (3, 0x02),
            "ALT": (3, 0x04),
            "VS": (3, 0x08),
        }
    )
    # bits per frame byte we have *not* named -- used to flag unmapped buttons
    known_mask: dict[int, int] = field(
        default_factory=lambda: {1: 0xF0, 2: 0xC3, 3: 0x0F, 4: 0x00}
    )


LAYOUT = Layout()


def _sbyte(v: int) -> int:
    """Interpret an unsigned byte as a signed 8-bit value."""
    return v - 256 if v > 127 else v


def normalize_frame(data: list[int]) -> list[int] | None:
    """Return a canonical frame (report id at [0]), or None for an empty read.

    Handles both hidapi behaviours: report id present, or stripped by the
    backend. Pads short frames so fixed-offset decoding is always safe.
    """
    if not data:
        return None
    frame = list(data)
    if frame[0] != REPORT_ID:
        frame = [REPORT_ID] + frame  # backend stripped the id
    if len(frame) < 8:
        frame += [0] * (8 - len(frame))
    return frame


@dataclass
class State:
    """Decoded snapshot of one input report."""

    mode: Mode = Mode.COM1
    buttons: frozenset[str] = frozenset()
    outer: int = 0  # signed delta this report
    inner: int = 0  # signed delta this report
    shift: bool = False  # SWAP held -> encoders address the shifted axis
    unknown_bits: tuple[tuple[int, int], ...] = ()  # (frame index, raw byte)
    raw: tuple[int, ...] = ()

    @classmethod
    def decode(cls, frame: list[int], layout: Layout = LAYOUT) -> "State":
        pressed = {
            name
            for name, (idx, mask) in layout.buttons.items()
            if idx < len(frame) and frame[idx] & mask
        }
        unknown = tuple(
            (idx, frame[idx])
            for idx, known in layout.known_mask.items()
            if idx < len(frame) and frame[idx] & ~known & 0xFF
        )
        return cls(
            mode=Mode.from_raw(frame[layout.mode]),
            buttons=frozenset(pressed),
            outer=_sbyte(frame[layout.outer]),
            inner=_sbyte(frame[layout.inner]),
            shift="SWAP" in pressed,
            unknown_bits=unknown,
            raw=tuple(frame[:8]),
        )


@dataclass
class Event:
    """Edge-triggered change between two consecutive States."""

    mode: Mode
    mode_changed: bool = False
    pressed: tuple[str, ...] = ()      # buttons that went down this report
    released: tuple[str, ...] = ()     # buttons that came up this report
    outer: int = 0                     # accumulated outer delta
    inner: int = 0                     # accumulated inner delta
    shift: bool = False
    long_press: tuple[str, ...] = ()   # buttons that just crossed LONG_PRESS_S

    def is_empty(self) -> bool:
        return not (
            self.mode_changed or self.pressed or self.released or self.outer
            or self.inner or self.long_press
        )


class IFR1:
    """Polling reader for the IFR-1.

        with IFR1() as dev:
            while True:
                for ev in dev.poll():
                    handle(ev)
    """

    def __init__(self, *, path: bytes | None = None, layout: Layout = LAYOUT):
        self._path = path
        self._layout = layout
        self._dev: hid.device | None = None
        self._prev: State | None = None
        self._led = 0
        self._press_time: dict[str, float] = {}   # button -> time.monotonic() pressed
        self._long_fired: set[str] = set()         # already emitted long_press this hold

    # -- lifecycle ------------------------------------------------------
    def open(self) -> "IFR1":
        d = hid.device()
        if self._path is not None:
            d.open_path(self._path)
        else:
            d.open(IFR1_VID, IFR1_PID)
        d.set_nonblocking(True)
        self._dev = d
        return self

    def close(self) -> None:
        if self._dev is not None:
            try:
                self.set_leds(0)
            except Exception:
                pass
            self._dev.close()
            self._dev = None

    def __enter__(self) -> "IFR1":
        return self.open()

    def __exit__(self, *exc) -> None:
        self.close()

    # -- input --------------------------------------------------------
    def read_raw(self) -> list[int] | None:
        """One normalized frame, or None if nothing is pending."""
        assert self._dev is not None
        return normalize_frame(self._dev.read(READ_SIZE))

    def read_state(self) -> State | None:
        frame = self.read_raw()
        return None if frame is None else State.decode(frame, self._layout)

    def poll(self) -> list[Event]:
        """Drain all pending reports; one Event per changed report, plus a
        synthetic long-press Event when a still-held button crosses
        `LONG_PRESS_S` (checked every call, even when the device sent nothing
        new - it only pushes on a state change)."""
        events: list[Event] = []
        now = time.monotonic()
        while True:
            st = self.read_state()
            if st is None:
                break
            now = time.monotonic()
            ev = self._diff(st, now)
            if ev is not None and not ev.is_empty():
                events.append(ev)
            self._prev = st
        held = self._check_holds(now)
        if held:
            events.append(Event(mode=self._prev.mode if self._prev else Mode.COM1,
                                 long_press=held))
        return events

    def _diff(self, st: State, now: float | None = None) -> Event | None:
        now = time.monotonic() if now is None else now
        prev = self._prev
        if prev is None:
            for b in st.buttons:
                self._press_time[b] = now
            return Event(mode=st.mode, mode_changed=True, shift=st.shift)
        newly_pressed = st.buttons - prev.buttons
        newly_released = prev.buttons - st.buttons
        for b in newly_pressed:
            self._press_time[b] = now
        for b in newly_released:
            self._press_time.pop(b, None)
            self._long_fired.discard(b)
        return Event(
            mode=st.mode,
            mode_changed=st.mode != prev.mode,
            pressed=tuple(sorted(newly_pressed)),
            released=tuple(sorted(newly_released)),
            outer=st.outer,
            inner=st.inner,
            shift=st.shift,
        )

    def _check_holds(self, now: float) -> tuple[str, ...]:
        """Buttons still held from `self._prev` whose press just crossed
        `LONG_PRESS_S` for the first time this hold."""
        if self._prev is None:
            return ()
        fired = []
        for b in self._prev.buttons:
            t0 = self._press_time.get(b)
            if t0 is not None and b not in self._long_fired and now - t0 >= LONG_PRESS_S:
                self._long_fired.add(b)
                fired.append(b)
        return tuple(sorted(fired))

    # -- output (LEDs) ------------------------------------------------
    def set_leds(self, bitmask: int) -> None:
        """AP-row LEDs: bit0 AP, bit1 HDG, bit2 NAV, bit3 APR, bit4 ALT, bit5 VS."""
        assert self._dev is not None
        self._led = bitmask & 0xFF
        self._dev.write([REPORT_ID, self._led])

    @property
    def leds(self) -> int:
        return self._led


def find_devices() -> list[dict]:
    return hid.enumerate(IFR1_VID, IFR1_PID)


def _open_or_die():
    devs = find_devices()
    if not devs:
        raise SystemExit(
            f"No IFR-1 found (VID {IFR1_VID:#06x} PID {IFR1_PID:#06x}). "
            "Plugged in? On Windows another app (X-Plane + the Octavi plugin, "
            "MobiFlight, the Octavi config tool) may hold it open - close them first."
        )
    for d in devs:
        print(
            f"found: path={d['path']!r} product={d.get('product_string')!r} "
            f"iface={d.get('interface_number')} usage={d.get('usage_page')}/{d.get('usage')}"
        )
    return IFR1().open()


def _raw() -> None:
    """Minimal diagnostic: dump every changed frame as hex + which index moved.

    Work each control in turn (mode selector through all 8 detents, outer knob
    both ways, inner knob both ways, then every button) and note which byte
    changes. Paste the output back to update `LAYOUT`. Ctrl+C to quit.
    """
    dev = _open_or_die()
    print("\nRAW MODE - operate one control at a time; watch which index changes.\n")
    prev: list[int] | None = None
    reported_len = False
    try:
        while True:
            data = dev._dev.read(READ_SIZE)  # unnormalized, exactly as hidapi gives it
            if data:
                if not reported_len:
                    print(f"(hidapi read() returned {len(data)} bytes; "
                          f"first byte {data[0]:#04x}"
                          f"{'  == REPORT_ID' if data[0] == REPORT_ID else '  (id stripped?)'})\n")
                    reported_len = True
                if data != prev:
                    hexs = " ".join(f"{b:02X}" for b in data[:12])
                    if prev is None:
                        diff = "first frame"
                    else:
                        diff = ", ".join(
                            f"[{i}] {prev[i]:02X}->{data[i]:02X}"
                            for i in range(min(len(prev), len(data)))
                            if prev[i] != data[i]
                        ) or "len change"
                    print(f"{hexs:<40}  {diff}")
                    prev = data
            time.sleep(0.005)
    except KeyboardInterrupt:
        print("\nbye")
    finally:
        dev.close()


def _explore() -> None:
    """Decoded view using the current LAYOUT. Ctrl+C to quit."""
    dev = _open_or_die()
    print("\nEXPLORE MODE - decoded with the current LAYOUT.")
    print("If a control does nothing / the wrong thing here but moves a byte in "
          "`raw` mode, the LAYOUT offsets are wrong.\n")
    last = ""
    try:
        while True:
            st = dev.read_state()
            if st is not None:
                raw = " ".join(f"{x:02X}" for x in st.raw)
                btn = ",".join(sorted(st.buttons)) or "-"
                unk = (
                    "  UNKNOWN " + " ".join(f"[{i}]={v:08b}" for i, v in st.unknown_bits)
                    if st.unknown_bits else ""
                )
                line = (f"[{raw}]  mode={st.mode.name:<4} outer={st.outer:+d} "
                        f"inner={st.inner:+d} btn={btn}{' +SHIFT' if st.shift else ''}{unk}")
                if line != last:
                    print(line)
                    last = line
            for ev in dev.poll():
                bits = []
                if ev.mode_changed:
                    bits.append(f"mode->{ev.mode.name}")
                if ev.pressed:
                    bits.append("down:" + "+".join(ev.pressed))
                if ev.released:
                    bits.append("up:" + "+".join(ev.released))
                if ev.outer:
                    bits.append(f"outer {ev.outer:+d}")
                if ev.inner:
                    bits.append(f"inner {ev.inner:+d}")
                if bits:
                    print("   event:", "  ".join(bits))
            time.sleep(0.01)
    except KeyboardInterrupt:
        print("\nbye")
    finally:
        dev.close()


_COMMANDS = {"raw": _raw, "explore": _explore}

if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "explore"
    fn = _COMMANDS.get(cmd)
    if fn is None:
        raise SystemExit(f"usage: python ifr1.py [{' | '.join(_COMMANDS)}]")
    fn()
