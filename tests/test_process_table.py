"""`win32.process_table` reads the kernel's process list in one call.

psutil answers `ppid()` on Windows with a snapshot of every process, one per
call, so discovery's parent walks paid one per hop: 126 of them, 3.3s of a
4.5s `status`. The table is that list read once. These tests feed the parser
a synthetic list rather than the machine's own.
"""
from __future__ import annotations

import ctypes

import pytest

import reloaded.win32 as win32_mod
from reloaded.win32 import _SYSTEM_PROCESS_INFORMATION as SPI

MISMATCH = win32_mod._STATUS_INFO_LENGTH_MISMATCH


def _fill(buf, rows):
    """Lay `rows` of (pid, ppid, name, filetime) out the way the kernel does:
    entries chained by NextEntryOffset, names stored beside them."""
    base, offset = ctypes.addressof(buf), 0
    stride = ctypes.sizeof(SPI) + 64
    for i, (pid, ppid, name, created) in enumerate(rows):
        entry = SPI.from_address(base + offset)
        entry.NextEntryOffset = stride if i + 1 < len(rows) else 0
        entry.CreateTime = created
        entry.UniqueProcessId = pid or None
        entry.InheritedFromUniqueProcessId = ppid or None
        if name:
            text = base + offset + ctypes.sizeof(SPI)
            ctypes.memmove(text, name.encode("utf-16-le"), 2 * len(name))
            entry.ImageName.Length = 2 * len(name)
            entry.ImageName.Buffer = text
        offset += stride


def _kernel(monkeypatch, rows, first=0):
    calls = []

    def query(cls, buf, size, needed):
        calls.append(size)
        if first and len(calls) == 1:
            needed._obj.value = size + 1
            return first
        _fill(buf, rows)
        return 0

    monkeypatch.setattr(win32_mod._ntdll, "NtQuerySystemInformation", query)
    return calls


EPOCH = win32_mod._FILETIME_EPOCH


def test_each_process_has_its_parent_name_and_start(monkeypatch):
    _kernel(monkeypatch, [(0, 0, "", 0),
                          (100, 4, "pwsh.exe", EPOCH + 10_000_000),
                          (200, 100, "claude.exe", EPOCH + 25_000_000)])

    table = win32_mod.process_table()

    assert table[100] == win32_mod.Proc(4, "pwsh.exe", 1.0)
    assert table[200] == win32_mod.Proc(100, "claude.exe", 2.5)
    assert table[0].name == ""


def test_a_list_that_grew_is_asked_for_again_with_room(monkeypatch):
    """Processes start between the size query and the read."""
    calls = _kernel(monkeypatch, [(100, 4, "pwsh.exe", EPOCH)], first=MISMATCH)

    assert 100 in win32_mod.process_table()
    assert len(calls) == 2 and calls[1] > calls[0]


def test_a_failed_read_raises_rather_than_reporting_no_processes(monkeypatch):
    """An empty table reads as every session having closed."""
    _kernel(monkeypatch, [], first=ctypes.c_long(0xC0000022).value)  # ACCESS_DENIED

    with pytest.raises(OSError, match="C0000022"):
        win32_mod.process_table()


@pytest.mark.skipif(ctypes.sizeof(ctypes.c_void_p) != 8, reason="x64 layout")
def test_the_fields_sit_where_winternl_puts_them():
    assert [getattr(SPI, f).offset for f in (
        "CreateTime", "ImageName", "UniqueProcessId", "InheritedFromUniqueProcessId")
    ] == [32, 56, 80, 88]
