# -*- coding: utf-8 -*-
"""
Screenshot method benchmark test.
测试各种截图方法的性能，包括: MINICAP_APK, MINICAP, JAVACAP, ADBCAP
"""

import time
import os
import pytest
import statistics
import json
from airtest.core.android.adb import ADB
from airtest.core.android.constant import CAP_METHOD
from airtest.core.api import init_device


class TestScreenshotBenchmark:
    """截图方法性能测试"""

    @pytest.fixture(scope="class")
    def adb(self):
        """获取ADB实例和设备序列号"""
        adb = ADB()
        devices = adb.devices()
        if not devices:
            pytest.skip("No device found")
        serialno = devices[0][0]
        return adb, serialno

    @pytest.fixture(scope="class")
    def output_dir(self, request):
        """创建输出目录"""
        output = os.path.join(os.path.dirname(__file__), "benchmark_results")
        os.makedirs(output, exist_ok=True)
        return output

    def _benchmark_single_method(self, adb, serialno, cap_method, count=20, warmup=3):
        """测试单个截图方法的性能"""
        device = None
        results = {
            "cap_method": cap_method,
            "method": None,
            "errors": [],
            "stream_times_ms": [],
            "snapshot_times_ms": [],
        }

        try:
            device = init_device(
                platform="Android",
                uuid=serialno,
                cap_method=cap_method
            )
            screen_method = device.screen_proxy.screen_method
            results["method"] = type(screen_method).__name__

            # 检查是否支持stream模式
            has_stream = hasattr(screen_method, 'get_frame_from_stream')

            # 预热
            for _ in range(warmup):
                screen_method.get_frame_from_stream()

            # 测试get_frame_from_stream (流模式)
            if has_stream:
                for i in range(count):
                    start = time.perf_counter()
                    frame = screen_method.get_frame_from_stream()
                    elapsed = (time.perf_counter() - start) * 1000

                    if frame is None:
                        results["errors"].append(f"stream frame {i} is None")
                        continue
                    results["stream_times_ms"].append(elapsed)

            # 测试snapshot (调用get_frame_from_stream)
            for i in range(count):
                start = time.perf_counter()
                screen = device.snapshot()
                elapsed = (time.perf_counter() - start) * 1000

                if screen is None:
                    results["errors"].append(f"snapshot {i} is None")
                    continue
                results["snapshot_times_ms"].append(elapsed)

        except Exception as e:
            results["errors"].append(str(e))
        finally:
            if device and hasattr(device, 'screen_proxy'):
                sp = device.screen_proxy
                if hasattr(sp.screen_method, 'teardown_stream'):
                    try:
                        sp.screen_method.teardown_stream()
                    except:
                        pass

        return results

    def _calc_stats(self, times_ms):
        """计算统计数据"""
        if not times_ms:
            return {}
        return {
            "count": len(times_ms),
            "avg_ms": round(statistics.mean(times_ms), 2),
            "min_ms": round(min(times_ms), 2),
            "max_ms": round(max(times_ms), 2),
            "stdev_ms": round(statistics.stdev(times_ms), 2) if len(times_ms) > 1 else 0,
        }

    @pytest.mark.parametrize("cap_method", [
        CAP_METHOD.MINICAP_APK,
        CAP_METHOD.MINICAP,
        CAP_METHOD.JAVACAP,
        CAP_METHOD.ADBCAP,
    ])
    def test_screenshot_performance(self, adb, cap_method, output_dir):
        """测试各截图方法的性能"""
        adb_instance, serialno = adb
        print(f"\n{'='*60}")
        print(f"Testing: {cap_method}")
        print(f"{'='*60}")

        results = self._benchmark_single_method(adb_instance, serialno, cap_method, count=20, warmup=3)

        # 打印结果
        print(f"Method: {results['method']}")
        
        if results["stream_times_ms"]:
            stream_stats = self._calc_stats(results["stream_times_ms"])
            print(f"Stream - Avg: {stream_stats['avg_ms']}ms, Min: {stream_stats['min_ms']}ms, Max: {stream_stats['max_ms']}ms, Std: {stream_stats['stdev_ms']}ms")
        
        if results["snapshot_times_ms"]:
            snapshot_stats = self._calc_stats(results["snapshot_times_ms"])
            print(f"Snapshot - Avg: {snapshot_stats['avg_ms']}ms, Min: {snapshot_stats['min_ms']}ms, Max: {snapshot_stats['max_ms']}ms, Std: {snapshot_stats['stdev_ms']}ms")

        if results["errors"]:
            print(f"Errors: {results['errors']}")

        # 保存结果
        result_file = os.path.join(output_dir, f"benchmark_{cap_method}_{int(time.time())}.json")
        with open(result_file, 'w', encoding='utf-8') as f:
            json.dump(results, f, indent=2, ensure_ascii=False)
        print(f"Results saved to: {result_file}")

        # 断言：至少要有snapshot结果
        assert results["snapshot_times_ms"], f"{cap_method}: No snapshot data captured"


if __name__ == '__main__':
    pytest.main([__file__, '-v', '-s'])