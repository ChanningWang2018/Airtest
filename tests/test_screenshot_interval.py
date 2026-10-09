# -*- coding: utf-8 -*-
"""
Screenshot method test: 5 snapshots with 1 second interval
记录每次snapshot的耗时情况
"""

import time
import os
from airtest.core.android.adb import ADB
from airtest.core.android.constant import CAP_METHOD
from airtest.core.api import init_device
from PIL import Image
import json


def test_screenshot_methods():
    """测试各截图方法，间隔1秒截图5张"""
    output_dir = os.path.join(os.path.dirname(__file__), "screenshot_test_results")
    os.makedirs(output_dir, exist_ok=True)
    
    adb = ADB()
    devices = adb.devices()
    if not devices:
        print("ERROR: No device found")
        return
    serialno = devices[0][0]
    print(f"Device: {serialno}")
    
    cap_methods = [
        CAP_METHOD.MINICAP,
        CAP_METHOD.MINICAP_APK,
        CAP_METHOD.JAVACAP,
        CAP_METHOD.ADBCAP,
    ]
    
    results = {}
    
    for cap_method in cap_methods:
        print(f"\n{'='*60}")
        print(f"Testing: {cap_method}")
        print(f"{'='*60}")
        
        method_times = []
        
        try:
            # 初始化设备
            device = init_device(
                platform="Android",
                uuid=serialno,
                cap_method=cap_method
            )
            
            screen_method = device.screen_proxy.screen_method
            actual_method = type(screen_method).__name__
            print(f"Actual method: {actual_method}")
            
            # 截图5次，无间隔（连续快速截图）
            for i in range(5):
                start_time = time.perf_counter()
                screen = device.snapshot()
                elapsed = time.perf_counter() - start_time
                elapsed_ms = elapsed * 1000
                
                method_times.append({
                    "index": i + 1,
                    "elapsed_ms": round(elapsed_ms, 2),
                    "screen_shape": screen.shape if screen is not None else None
                })
                
                # 保存图片
                if screen is not None:
                    img_path = os.path.join(output_dir, f"{cap_method}_{i+1}.jpg")
                    Image.fromarray(screen).save(img_path, "JPEG")
                    print(f"  Snapshot {i+1}/5: {elapsed_ms:.2f}ms -> {img_path}")
                else:
                    print(f"  Snapshot {i+1}/5: FAILED (returned None)")
                
                # 无间隔
                
            # 清理
            if hasattr(screen_method, 'teardown_stream'):
                screen_method.teardown_stream()
                
            results[cap_method] = {
                "actual_method": actual_method,
                "snapshots": method_times,
                "avg_ms": round(sum(t["elapsed_ms"] for t in method_times) / len(method_times), 2),
                "min_ms": round(min(t["elapsed_ms"] for t in method_times), 2),
                "max_ms": round(max(t["elapsed_ms"] for t in method_times), 2),
            }
            
        except Exception as e:
            print(f"  ERROR: {e}")
            results[cap_method] = {"error": str(e)}
        
        # 清理设备
        try:
            if 'device' in locals():
                del device
        except:
            pass
    
    # 打印汇总结果
    print(f"\n{'='*60}")
    print("RESULTS SUMMARY")
    print(f"{'='*60}")
    print(f"{'Method':<20} {'Avg(ms)':<12} {'Min(ms)':<12} {'Max(ms)':<12}")
    print("-" * 56)
    
    for method, data in results.items():
        if "error" in data:
            print(f"{method:<20} ERROR: {data['error'][:40]}")
        else:
            print(f"{data['actual_method']:<20} {data['avg_ms']:<12} {data['min_ms']:<12} {data['max_ms']:<12}")
    
    # 保存详细结果
    result_file = os.path.join(output_dir, f"screenshot_test_{int(time.time())}.json")
    with open(result_file, 'w', encoding='utf-8') as f:
        json.dump(results, f, indent=2, ensure_ascii=False)
    print(f"\nDetailed results saved to: {result_file}")
    
    return results


if __name__ == '__main__':
    test_screenshot_methods()