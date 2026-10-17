"""Phase 0 proof of concept: read the HS80 Max battery through the official iCUE SDK v4
(CorsairOfficial/cue-sdk, iCUESDK.x64_2019.dll) with ctypes.

Needs iCUE running and "Enable software integrations" (SDK / third-party control)
turned on in iCUE Settings. iCUE must be at the same privilege level as this script.

Usage:
    python poc_icue.py [--dll PATH\\to\\iCUESDK.x64_2019.dll]
Default DLL search: sdk\\**\\iCUESDK.x64_2019.dll under this folder, then the folder itself.
"""
from __future__ import annotations

import argparse
import ctypes as C
import glob
import os
import sys
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))

# ---- iCUESDK.h (v4) constants -------------------------------------------------------
CORSAIR_STRING_SIZE_M = 128
CORSAIR_DEVICE_ID_MAX = 128
CORSAIR_DEVICE_COUNT_MAX = 64

ERRORS = ["CE_Success", "CE_NotConnected", "CE_NoControl", "CE_IncompatibleProtocol",
          "CE_InvalidArguments", "CE_InvalidOperation", "CE_DeviceNotFound", "CE_NotAllowed"]
STATES = ["CSS_Invalid", "CSS_Closed", "CSS_Connecting", "CSS_Timeout",
          "CSS_ConnectionRefused", "CSS_ConnectionLost", "CSS_Connected"]
CSS_CONNECTED = 6
CDT_HEADSET = 0x0008
CDPI_BATTERY_LEVEL = 9
CDPI_MIC_ENABLED = 2
DATA_TYPES = ["CT_Boolean", "CT_Int32", "CT_Float64", "CT_String",
              "CT_Boolean_Array", "CT_Int32_Array", "CT_Float64_Array", "CT_String_Array"]
DEVICE_TYPES = {0x1: "Keyboard", 0x2: "Mouse", 0x4: "Mousemat", 0x8: "Headset", 0x10: "HeadsetStand",
                0x20: "FanLedController", 0x40: "LedController", 0x80: "MemoryModule", 0x100: "Cooler",
                0x200: "Motherboard", 0x400: "GraphicsCard", 0x800: "Touchbar", 0x1000: "GameController"}


class CorsairVersion(C.Structure):
    _fields_ = [("major", C.c_int), ("minor", C.c_int), ("patch", C.c_int)]


class CorsairSessionDetails(C.Structure):
    _fields_ = [("clientVersion", CorsairVersion), ("serverVersion", CorsairVersion),
                ("serverHostVersion", CorsairVersion)]


class CorsairSessionStateChanged(C.Structure):
    _fields_ = [("state", C.c_int), ("details", CorsairSessionDetails)]


class CorsairDeviceFilter(C.Structure):
    _fields_ = [("deviceTypeMask", C.c_int)]


class CorsairDeviceInfo(C.Structure):
    _fields_ = [("type", C.c_int),
                ("id", C.c_char * CORSAIR_DEVICE_ID_MAX),
                ("serial", C.c_char * CORSAIR_STRING_SIZE_M),
                ("model", C.c_char * CORSAIR_STRING_SIZE_M),
                ("ledCount", C.c_int),
                ("channelCount", C.c_int)]


class _BoolArray(C.Structure):
    _fields_ = [("items", C.POINTER(C.c_bool)), ("count", C.c_uint)]


class _Int32Array(C.Structure):
    _fields_ = [("items", C.POINTER(C.c_int)), ("count", C.c_uint)]


class _Float64Array(C.Structure):
    _fields_ = [("items", C.POINTER(C.c_double)), ("count", C.c_uint)]


class _StringArray(C.Structure):
    _fields_ = [("items", C.POINTER(C.c_char_p)), ("count", C.c_uint)]


class CorsairDataValue(C.Union):
    _fields_ = [("boolean", C.c_bool), ("int32", C.c_int), ("float64", C.c_double),
                ("string", C.c_char_p), ("boolean_array", _BoolArray), ("int32_array", _Int32Array),
                ("float64_array", _Float64Array), ("string_array", _StringArray)]


class CorsairProperty(C.Structure):
    _fields_ = [("type", C.c_int), ("value", CorsairDataValue)]


STATE_CB = C.CFUNCTYPE(None, C.c_void_p, C.POINTER(CorsairSessionStateChanged))


def err_name(e: int) -> str:
    return ERRORS[e] if 0 <= e < len(ERRORS) else f"CorsairError({e})"


def find_dll(explicit: str | None) -> str:
    cands = [explicit] if explicit else []
    cands += glob.glob(os.path.join(HERE, "sdk", "**", "iCUESDK.x64_2019.dll"), recursive=True)
    cands += glob.glob(os.path.join(HERE, "iCUESDK*.dll"))
    for c in cands:
        if c and os.path.isfile(c):
            return c
    raise SystemExit("iCUESDK.x64_2019.dll not found. Download iCUESDK_4.0.84.zip from "
                     "https://github.com/CorsairOfficial/cue-sdk/releases and unzip it to .\\sdk\\, "
                     "or pass --dll.")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dll")
    ap.add_argument("--timeout", type=float, default=10.0, help="seconds to wait for CSS_Connected")
    args = ap.parse_args()

    dll_path = find_dll(args.dll)
    print(f"DLL: {dll_path}")
    sdk = C.CDLL(dll_path)   # x64: cdecl/stdcall are the same ABI

    sdk.CorsairConnect.restype = C.c_int
    sdk.CorsairConnect.argtypes = [STATE_CB, C.c_void_p]
    sdk.CorsairDisconnect.restype = C.c_int
    sdk.CorsairGetSessionDetails.restype = C.c_int
    sdk.CorsairGetSessionDetails.argtypes = [C.POINTER(CorsairSessionDetails)]
    sdk.CorsairGetDevices.restype = C.c_int
    sdk.CorsairGetDevices.argtypes = [C.POINTER(CorsairDeviceFilter), C.c_int,
                                      C.POINTER(CorsairDeviceInfo), C.POINTER(C.c_int)]
    sdk.CorsairReadDeviceProperty.restype = C.c_int
    sdk.CorsairReadDeviceProperty.argtypes = [C.c_char_p, C.c_int, C.c_uint, C.POINTER(CorsairProperty)]
    sdk.CorsairFreeProperty.restype = C.c_int
    sdk.CorsairFreeProperty.argtypes = [C.POINTER(CorsairProperty)]

    connected = threading.Event()
    last_state = {"state": None}

    @STATE_CB
    def on_state(_ctx, ev):
        st = ev.contents.state
        last_state["state"] = st
        d = ev.contents.details
        print(f"  session state -> {STATES[st] if 0 <= st < len(STATES) else st}  "
              f"(client {d.clientVersion.major}.{d.clientVersion.minor}.{d.clientVersion.patch}, "
              f"server {d.serverVersion.major}.{d.serverVersion.minor}.{d.serverVersion.patch}, "
              f"iCUE {d.serverHostVersion.major}.{d.serverHostVersion.minor}.{d.serverHostVersion.patch})")
        if st == CSS_CONNECTED:
            connected.set()

    t0 = time.perf_counter()
    e = sdk.CorsairConnect(on_state, None)
    print(f"CorsairConnect -> {err_name(e)}")
    if e != 0:
        return 2
    if not connected.wait(args.timeout):
        st = last_state["state"]
        print(f"not connected after {args.timeout:g} s (last state: "
              f"{STATES[st] if st is not None and 0 <= st < len(STATES) else st}).")
        print("If the state is CSS_ConnectionRefused or stays CSS_Connecting: enable "
              "'Enable software integrations' (SDK) in iCUE Settings and make sure iCUE runs "
              "at the same privilege level as this script.")
        sdk.CorsairDisconnect()
        return 3
    print(f"connected in {(time.perf_counter() - t0) * 1000:.0f} ms")

    try:
        filt = CorsairDeviceFilter(CDT_HEADSET)
        devs = (CorsairDeviceInfo * CORSAIR_DEVICE_COUNT_MAX)()
        n = C.c_int(0)
        e = sdk.CorsairGetDevices(C.byref(filt), CORSAIR_DEVICE_COUNT_MAX, devs, C.byref(n))
        print(f"CorsairGetDevices(CDT_Headset) -> {err_name(e)}, {n.value} device(s)")
        if e != 0:
            return 4
        if n.value == 0:
            filt = CorsairDeviceFilter(-1)   # CDT_All
            e = sdk.CorsairGetDevices(C.byref(filt), CORSAIR_DEVICE_COUNT_MAX, devs, C.byref(n))
            print(f"CorsairGetDevices(CDT_All) -> {err_name(e)}, {n.value} device(s):")
            for i in range(n.value):
                d = devs[i]
                print(f"  type=0x{d.type:x} ({DEVICE_TYPES.get(d.type, '?')}) model={d.model.decode(errors='replace')!r} "
                      f"id={d.id.decode(errors='replace')!r}")
            print("no CDT_Headset device reported by iCUE")
            return 5

        rc = 0
        for i in range(n.value):
            d = devs[i]
            print(f"  headset: model={d.model.decode(errors='replace')!r} id={d.id.decode(errors='replace')!r} "
                  f"serial={d.serial.decode(errors='replace')!r} leds={d.ledCount}")
            for name, pid in (("CDPI_BatteryLevel", CDPI_BATTERY_LEVEL), ("CDPI_MicEnabled", CDPI_MIC_ENABLED)):
                prop = CorsairProperty()
                t1 = time.perf_counter()
                e = sdk.CorsairReadDeviceProperty(d.id, pid, 0, C.byref(prop))
                dt = (time.perf_counter() - t1) * 1000
                if e != 0:
                    print(f"    {name}: {err_name(e)} ({dt:.0f} ms)")
                    if pid == CDPI_BATTERY_LEVEL:
                        rc = 6
                    continue
                tname = DATA_TYPES[prop.type] if 0 <= prop.type < len(DATA_TYPES) else prop.type
                if prop.type == 1:
                    val = prop.value.int32
                elif prop.type == 0:
                    val = prop.value.boolean
                elif prop.type == 2:
                    val = prop.value.float64
                elif prop.type == 3:
                    val = prop.value.string
                else:
                    val = "<array>"
                print(f"    {name}: {val} ({tname}, {dt:.0f} ms)")
                if pid == CDPI_BATTERY_LEVEL:
                    print(f"\nbattery (iCUE SDK): {val}%")
                sdk.CorsairFreeProperty(C.byref(prop))
        return rc
    finally:
        sdk.CorsairDisconnect()


if __name__ == "__main__":
    sys.exit(main())
