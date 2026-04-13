# -*- coding: utf-8 -*-
"""Quick test for get_frame"""
from airtest.core.android.adb import ADB
from airtest.core.android.cap_methods.minicap_apk import MinicapApk

adb = ADB()
devices = adb.devices()
print("Devices:", devices)
if devices:
    serialno = devices[0][0]
    print("Using:", serialno)
    adb.serialno = serialno
    mc = MinicapApk(adb)
    try:
        frame = mc.get_frame()
        print("SUCCESS: got frame, size =", len(frame))
    except Exception as e:
        print("ERROR:", e)
else:
    print("No devices")