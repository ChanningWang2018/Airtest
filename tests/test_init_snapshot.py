# -*- coding: utf-8 -*-
"""
Test init_device with MINICAP_APK and continuous snapshots
"""
from airtest.core.api import init_device
from airtest.core.android.adb import ADB
from airtest.core.android.constant import CAP_METHOD
import time
import os
from PIL import Image
from airtest.aircv.utils import string_2_img


def test_init_and_snapshot():
    """Test init_device and continuous snapshots"""
    adb = ADB()
    devices = adb.devices()
    if not devices:
        raise RuntimeError("No device found")
    serialno = devices[0][0]
    
    print("=== Init device with MINICAP_APK ===")
    device = init_device(
        platform="Android",
        uuid=serialno,
        cap_method="MINICAP_APK"
    )
    
    print(f"Device: {serialno}")
    print(f"Cap method: {device._cap_method}")
    
    # Check screen_proxy
    screen_proxy = device.screen_proxy
    screen_method = screen_proxy.screen_method
    print(f"Screen method: {type(screen_method).__name__}")
    
    # Capture 10 screenshots at 0.2s interval
    output_dir = os.path.join(os.path.dirname(__file__), "snapshot_test")
    os.makedirs(output_dir, exist_ok=True)
    
    print(f"\n=== Capturing 10 screenshots at 0.2s interval ===")
    
    for i in range(10):
        start = time.time()
        
        # Take snapshot
        screen = device.snapshot()
        
        elapsed = time.time() - start
        
        if screen is not None:
            # Save image
            filepath = os.path.join(output_dir, f"snapshot_{i:03d}.jpg")
            Image.fromarray(screen).save(filepath, "JPEG")
            print(f"Snapshot {i}: shape={screen.shape}, time={elapsed:.3f}s -> {filepath}")
        else:
            print(f"Snapshot {i}: FAILED (returned None)")
        
        # Wait 0.2s between shots
        if i < 9:
            time.sleep(0.2)
    
    print("\n=== Test completed ===")
    return True


if __name__ == '__main__':
    test_init_and_snapshot()