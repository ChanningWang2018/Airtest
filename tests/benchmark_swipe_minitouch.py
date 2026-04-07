# -*- coding: utf-8 -*-
"""
Benchmark script for minitouch swipe performance with duration=0.1s
Uses force_minitouch=True to bypass Android 10+ maxtouch enforcement.
"""
import time
import statistics
from airtest.core.android.android import Android
from airtest.core.android.constant import TOUCH_METHOD
from airtest.core.android.adb import ADB


def benchmark_swipe(num_runs=20):
    """
    Benchmark minitouch swipe with duration=0.1s using Android device object.
    """
    # Get device serial
    adb = ADB()
    devices = adb.devices()
    if not devices:
        raise RuntimeError("At least one adb device required")
    
    serialno = devices[0][0]
    print(f"Connecting to device: {serialno}")
    
    # Initialize Android device with force_minitouch=True to bypass Android 10+ restriction
    device = Android(serialno=serialno, touch_method=TOUCH_METHOD.MINITOUCH, force_minitouch=True)
    
    print(f"SDK version: {device.sdk_version}")
    print(f"Touch method in use: {device._touch_method}")
    
    try:
        durations = []
        
        print(f"\nRunning {num_runs} swipe tests with duration=0.1s...")
        print(f"From: (100, 100) -> To: (500, 500)")
        print("-" * 50)
        
        for i in range(num_runs):
            start = time.perf_counter()
            device.swipe((100, 100), (500, 500), duration=0.1, steps=5)
            elapsed = (time.perf_counter() - start) * 1000  # Convert to ms
            durations.append(elapsed)
            print(f"Run {i+1:2d}: {elapsed:7.2f} ms")
        
        print("-" * 50)
        print(f"\nResults ({num_runs} runs):")
        print(f"  Min:     {min(durations):.2f} ms")
        print(f"  Max:     {max(durations):.2f} ms")
        print(f"  Mean:    {statistics.mean(durations):.2f} ms")
        print(f"  Median:  {statistics.median(durations):.2f} ms")
        print(f"  StdDev:  {statistics.stdev(durations):.2f} ms")
        
    finally:
        if device.minitouch:
            device.minitouch.teardown()


if __name__ == '__main__':
    benchmark_swipe()
