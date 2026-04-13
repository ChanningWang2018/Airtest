# -*- coding: utf-8 -*-
"""
Test init_device with MINICAP_APK screenshot and MAXTOUCH touch
"""
import pytest
from airtest.core.api import init_device
from airtest.core.android.adb import ADB
from airtest.core.android.constant import CAP_METHOD, TOUCH_METHOD


def test_minicap_apk_cap_method():
    """Test init_device with MINICAP_APK cap method"""
    adb = ADB()
    devices = adb.devices()
    if not devices:
        raise RuntimeError("At least one adb device required")
    serialno = devices[0][0]
    
    print("=== Test MINICAP_APK cap method ===")
    print(f"Device: {serialno}")
    
    # Init device with MINICAP_APK
    device = init_device(
        platform="Android", 
        uuid=serialno, 
        cap_method="MINICAP_APK"
    )
    
    print(f"Cap method: {device._cap_method}")
    
    # Verify cap method is MINICAP_APK
    assert device._cap_method == CAP_METHOD.MINICAP_APK, \
        f"Expected MINICAP_APK but got {device._cap_method}"
    
    # Verify screen_proxy is set up
    screen_proxy = device.screen_proxy
    assert screen_proxy is not None, "screen_proxy is None"
    
    screen_method = screen_proxy.screen_method
    print(f"Screen method: {type(screen_method).__name__}")
    
    # This will FAIL if minicap_apk setup failed (will fallback to AdbCap)
    is_minicap_apk = type(screen_method).__name__ == "MinicapApk"
    print(f"Is MinicapApk: {is_minicap_apk}")
    
    # Print the failure message if it's not MinicapApk
    if not is_minicap_apk:
        print(f"FAILED: Screen method is {type(screen_method).__name__}, not MinicapApk")
        print("This means minicap_apk setup failed and fell back to another method")
    else:
        print("PASSED: MinicapApk is working!")
    
    return is_minicap_apk


if __name__ == '__main__':
    result = test_minicap_apk_cap_method()
    if not result:
        exit(1)
    else:
        exit(0)