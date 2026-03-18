"""Windows key input helpers for gear up/down commands."""

from __future__ import annotations

import ctypes
import time
from dataclasses import dataclass

KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_SCANCODE = 0x0008
INPUT_KEYBOARD = 1

# Hardware scan codes (set 1) — recognised by DirectInput/games
SC_Q = 0x10
SC_E = 0x12


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", ctypes.c_ushort),
        ("wScan", ctypes.c_ushort),
        ("dwFlags", ctypes.c_ulong),
        ("time", ctypes.c_ulong),
        ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong)),
    ]


class MOUSEINPUT(ctypes.Structure):
    """Included to force INPUT_UNION to the correct 32-byte size on 64-bit Windows."""

    _fields_ = [
        ("dx", ctypes.c_long),
        ("dy", ctypes.c_long),
        ("mouseData", ctypes.c_ulong),
        ("dwFlags", ctypes.c_ulong),
        ("time", ctypes.c_ulong),
        ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong)),
    ]


class INPUT_UNION(ctypes.Union):
    _fields_ = [("ki", KEYBDINPUT), ("mi", MOUSEINPUT)]


class INPUT(ctypes.Structure):
    _fields_ = [("type", ctypes.c_ulong), ("union", INPUT_UNION)]


@dataclass(slots=True)
class GearInputConfig:
    key_hold_seconds: float = 0.03
    dry_run: bool = False


class GearInputController:
    """Sends gear up/down keypresses using Win32 SendInput."""

    def __init__(self, config: GearInputConfig | None = None) -> None:
        self.config = config or GearInputConfig()
        self._send_input = ctypes.windll.user32.SendInput

    def shift_up(self) -> None:
        self._press_scan_key(SC_E)

    def shift_down(self) -> None:
        self._press_scan_key(SC_Q)

    def _press_scan_key(self, scan_code: int) -> None:
        if self.config.dry_run:
            return

        self._send_key_event(scan_code, KEYEVENTF_SCANCODE)
        time.sleep(self.config.key_hold_seconds)
        self._send_key_event(scan_code, KEYEVENTF_SCANCODE | KEYEVENTF_KEYUP)

    def _send_key_event(self, scan_code: int, flags: int) -> None:
        extra = ctypes.c_ulong(0)
        keyboard_input = KEYBDINPUT(
            wVk=0,
            wScan=scan_code,
            dwFlags=flags,
            time=0,
            dwExtraInfo=ctypes.pointer(extra),
        )
        input_struct = INPUT(type=INPUT_KEYBOARD, union=INPUT_UNION(ki=keyboard_input))
        self._send_input(1, ctypes.byref(input_struct), ctypes.sizeof(INPUT))
