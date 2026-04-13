# Quick debug
import os, sys
sys.path.insert(0, "E:/projects/airtest/Airtest")
os.chdir("E:/projects/airtest/Airtest")

print("Starting...")

from airtest.core.android.adb import ADB
print("ADB imported")

adb = ADB()
print("ADB created")

devices = adb.devices()
print("Devices:", devices)

if not devices:
    print("No devices")
    sys.exit(0)

serialno = devices[0][0]
print("Using:", serialno)
adb.serialno = serialno

print("Creating minicap...")
from airtest.core.android.cap_methods.minicap_apk import MinicapApk
mc = MinicapApk(adb)

print("Calling get_frame (10s timeout)...")

# Add timeout to the shell command
import socket
old_connect = socket.socket.connect

def new_connect(self, addr):
    print(f"Connecting to {addr}")
    old_connect(self, addr)
    self.settimeout(10)

socket.socket.connect = new_connect

try:
    frame = mc.get_frame()
    print("SUCCESS:", len(frame))
except Exception as e:
    print("ERROR:", e)
    import traceback
    traceback.print_exc()
    
print("Done")