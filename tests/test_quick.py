# -*- coding: utf-8 -*-
"""Very quick test"""
import sys
sys.path.insert(0, "E:/projects/airtest/Airtest")

from airtest.core.android.adb import ADB
from airtest.core.android.cap_methods.minicap_apk import MinicapApk

try:
    adb = ADB()
    devices = adb.devices()
    if not devices:
        print("No devices")
        sys.exit(1)
    
    serialno = devices[0][0]
    adb.serialno = serialno
    
    print("Creating MinicapApk...")
    mc = MinicapApk(adb)
    
    print("Calling get_frame()...")
    frame = mc.get_frame()
    
    print("SUCCESS: frame size =", len(frame))
except Exception as e:
    import traceback
    print("ERROR:", e)
    traceback.print_exc()
except:
    print("Other error")