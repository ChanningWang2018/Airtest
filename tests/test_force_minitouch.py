# -*- coding: utf-8 -*-
"""
Test force_minitouch parameter via init_device and connect_device
"""
from airtest.core.api import init_device, connect_device, G
from airtest.core.android.constant import TOUCH_METHOD
from airtest.core.android.adb import ADB


def test_init_device():
    """Test init_device with force_minitouch=True"""
    adb = ADB()
    devices = adb.devices()
    if not devices:
        raise RuntimeError("At least one adb device required")
    serialno = devices[0][0]
    
    print("=== Test init_device with force_minitouch=True ===")
    device = init_device(platform="Android", uuid=serialno, touch_method="MINITOUCH", force_minitouch=True)
    print(f"SDK version: {device.sdk_version}")
    print(f"Touch method: {device._touch_method}")
    assert device._touch_method == TOUCH_METHOD.MINITOUCH, f"Expected MINITOUCH but got {device._touch_method}"
    print("PASSED: init_device with force_minitouch=True works!")
    return device


def test_connect_device():
    """Test connect_device with force_minitouch=True"""
    adb = ADB()
    devices = adb.devices()
    if not devices:
        raise RuntimeError("At least one adb device required")
    serialno = devices[0][0]
    
    print("\n=== Test connect_device with force_minitouch=True ===")
    uri = f"Android:///{serialno}?touch_method=MINITOUCH&force_minitouch=True"
    print(f"URI: {uri}")
    device = connect_device(uri)
    print(f"SDK version: {device.sdk_version}")
    print(f"Touch method: {device._touch_method}")
    assert device._touch_method == TOUCH_METHOD.MINITOUCH, f"Expected MINITOUCH but got {device._touch_method}"
    print("PASSED: connect_device with force_minitouch=True works!")
    return device


def test_swipe(device):
    """Test swipe actually works"""
    print("\n=== Test swipe via touch_proxy ===")
    proxy = device.touch_proxy
    print(f"Touch proxy type: {type(proxy).__name__}")
    
    import time
    start = time.perf_counter()
    device.swipe((100, 100), (500, 500), duration=0.1, steps=5)
    elapsed = (time.perf_counter() - start) * 1000
    print(f"Swipe completed in {elapsed:.2f} ms")
    print("PASSED: swipe works!")


if __name__ == '__main__':
    # Test init_device
    dev1 = test_init_device()
    test_swipe(dev1)
    if dev1.minitouch:
        dev1.minitouch.teardown()
    
    # Test connect_device
    dev2 = test_connect_device()
    test_swipe(dev2)
    if dev2.minitouch:
        dev2.minitouch.teardown()
    
    print("\n=== All tests passed! ===")
