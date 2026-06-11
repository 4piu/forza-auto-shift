"""Windows key input helpers for gear up/down commands."""

from __future__ import annotations

import ctypes
import time
from dataclasses import dataclass

DWORD = ctypes.c_uint32
ULONG_PTR = ctypes.c_size_t

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
        ("dwFlags", DWORD),
        ("time", DWORD),
        ("dwExtraInfo", ULONG_PTR),
    ]


class MOUSEINPUT(ctypes.Structure):
    """Included to force INPUT_UNION to the correct 32-byte size on 64-bit Windows."""

    _fields_ = [
        ("dx", ctypes.c_long),
        ("dy", ctypes.c_long),
        ("mouseData", DWORD),
        ("dwFlags", DWORD),
        ("time", DWORD),
        ("dwExtraInfo", ULONG_PTR),
    ]


class INPUT_UNION(ctypes.Union):
    _fields_ = [("ki", KEYBDINPUT), ("mi", MOUSEINPUT)]


class INPUT(ctypes.Structure):
    _fields_ = [("type", DWORD), ("union", INPUT_UNION)]


@dataclass(slots=True)
class GearInputConfig:
    key_hold_seconds: float = 0.03
    dry_run: bool = False
    shift_down_scan_code: int = SC_Q
    shift_up_scan_code: int = SC_E


class GearInputController:
    """Sends gear up/down keypresses using Win32 SendInput."""

    def __init__(self, config: GearInputConfig | None = None) -> None:
        self.config = config or GearInputConfig()
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        self._send_input = user32.SendInput
        self._send_input.argtypes = (
            ctypes.c_uint,
            ctypes.POINTER(INPUT),
            ctypes.c_int,
        )
        self._send_input.restype = ctypes.c_uint

    def shift_up(self) -> None:
        self._press_scan_key(self.config.shift_up_scan_code)

    def shift_down(self) -> None:
        self._press_scan_key(self.config.shift_down_scan_code)

    def _press_scan_key(self, scan_code: int) -> None:
        if self.config.dry_run:
            return

        self._send_key_event(scan_code, KEYEVENTF_SCANCODE)
        time.sleep(self.config.key_hold_seconds)
        self._send_key_event(scan_code, KEYEVENTF_SCANCODE | KEYEVENTF_KEYUP)

    def _send_key_event(self, scan_code: int, flags: int) -> None:
        keyboard_input = KEYBDINPUT(
            wVk=0,
            wScan=scan_code,
            dwFlags=flags,
            time=0,
            dwExtraInfo=0,
        )
        input_struct = INPUT(type=INPUT_KEYBOARD, union=INPUT_UNION(ki=keyboard_input))
        sent = self._send_input(1, ctypes.byref(input_struct), ctypes.sizeof(INPUT))
        if sent != 1:
            raise ctypes.WinError(ctypes.get_last_error())
