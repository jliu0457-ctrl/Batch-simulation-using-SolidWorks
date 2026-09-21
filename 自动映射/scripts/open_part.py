#!/usr/bin/env python3
"""Open (or re-open) a SolidWorks document by path, closing whatever is active first.

Usage:  python open_part.py <path>

Why this exists: .NET's Marshal.GetActiveObject only attaches once SolidWorks has a document
open, while pywin32 attaches either way.  So the C# probes are driven from here - python opens
the document, then the probe attaches and reads the geometry.
"""
import sys
import time

import pythoncom
import win32com.client as win32


def attach(retries=30, delay=3.0):
    last = None
    for _ in range(retries):
        try:
            return win32.GetActiveObject("SldWorks.Application")
        except Exception as exc:  # noqa: BLE001
            last = exc
            time.sleep(delay)
    raise RuntimeError(f"could not attach to SolidWorks: {last}")


def main():
    if len(sys.argv) < 2:
        print("usage: open_part.py <path>")
        return 2
    path = sys.argv[1]
    pythoncom.CoInitialize()
    sw = attach()
    current = sw.ActiveDoc
    if current is not None:
        title = current.GetTitle
        print("closing:", title)
        sw.CloseDoc(title)
    result = sw.OpenDoc6(path, 1, 1, "", 0, 0)
    doc, errors, warnings = (list(result) + [None, None])[:3] if isinstance(result, tuple) else (result, None, None)
    print("opened:", doc is not None, "errors:", errors, "warnings:", warnings)
    active = sw.ActiveDoc
    print("active:", active.GetTitle if active is not None else None)
    return 0 if doc is not None else 1


if __name__ == "__main__":
    sys.exit(main())
