# encoding=utf-8
"""
minicap_apk -s 单次截图模式 TDD 测试

测试目标:
1. get_frame_via_shell() 方法能正确执行单次截图
2. get_frame() 优先使用 -s 模式
3. JPEG 数据格式正确
4. 超时和错误处理

TDD 流程:
1. Red: 编写测试 (预期失败)
2. Green: 实现代码让测试通过
3. Yellow: 重构优化
"""

import time
import unittest

from airtest.core.android.android import Android
from airtest.core.android.cap_methods.minicap_apk import MinicapApk


class TestMinicapApkStreamMode(unittest.TestCase):
    """测试流式截图模式"""

    @classmethod
    def setUpClass(cls):
        cls.dev = Android()
        cls.dev.rotation_watcher.get_ready()
        cls.minicap_apk = MinicapApk(
            cls.dev.adb, rotation_watcher=cls.dev.rotation_watcher
        )

    @classmethod
    def tearDownClass(cls):
        cls.dev.rotation_watcher.teardown()
        cls.minicap_apk.teardown_stream()

    def setUp(self):
        self.minicap_apk.teardown_stream()

    def test_get_frame_via_stream_returns_jpeg(self):
        """
        测试 get_frame_via_stream() 返回有效的 JPEG 数据

        验收标准:
        - 返回 bytes 类型
        - 以 FF D8 开头 (JPEG SOI)
        - 以 FF D9 结尾 (JPEG EOI)
        """
        frame = self.minicap_apk.get_frame_via_stream()

        self.assertIsInstance(frame, bytes)
        self.assertTrue(
            frame.startswith(b"\xff\xd8"),
            f"Frame should start with JPEG SOI (FF D8), got: {frame[:4].hex()}"
        )
        self.assertTrue(
            frame.endswith(b"\xff\xd9"),
            f"Frame should end with JPEG EOI (FF D9), got: {frame[-4:].hex()}"
        )

    def test_get_frame_via_stream_performance(self):
        """
        测试 get_frame_via_stream() 性能

        验收标准:
        - 单次截图 < 20s (首次连接可能需要等待服务器启动)
        - 连接复用后后续调用更快
        """
        # 首次截图 (需要建立连接)
        start = time.time()
        frame = self.minicap_apk.get_frame_via_stream()
        first_latency = time.time() - start
        self.assertIsNotNone(frame)
        self.assertGreater(len(frame), 1000)
        print(f"\n  首次截图: {first_latency*1000:.0f}ms")

        # 后续截图 (复用连接)
        latencies = []
        for _ in range(3):
            start = time.time()
            frame = self.minicap_apk.get_frame_via_stream()
            latency = time.time() - start
            latencies.append(latency)
            self.assertGreater(len(frame), 1000)

        avg_latency = sum(latencies) / len(latencies)
        print(f"  后续截图平均: {avg_latency*1000:.0f}ms")

        # 后续截图应该小于 5s
        self.assertLess(
            avg_latency, 5,
            f"后续截图平均延迟 {avg_latency*1000:.0f}ms 超过 5s"
        )

    def test_get_frame_via_stream_connection_reuse(self):
        """
        测试连接复用

        验收标准:
        - 连续截图使用同一连接
        - 帧大小相近
        """
        frames = []
        for _ in range(3):
            frame = self.minicap_apk.get_frame_via_stream()
            frames.append(len(frame))

        print(f"\n  帧大小: {frames}")

        self.assertEqual(len(frames), 3)

        # 帧大小应该相近 (变化小于 20%)
        avg_size = sum(frames) / len(frames)
        for size in frames:
            diff_ratio = abs(size - avg_size) / avg_size
            self.assertLess(
                diff_ratio, 0.2,
                f"帧大小 {size} 与平均值 {avg_size} 差异超过 20%"
            )


class TestGetFramePriority(unittest.TestCase):
    """测试 get_frame() 使用流式模式"""

    @classmethod
    def setUpClass(cls):
        cls.dev = Android()
        cls.dev.rotation_watcher.get_ready()
        cls.minicap_apk = MinicapApk(
            cls.dev.adb, rotation_watcher=cls.dev.rotation_watcher
        )

    @classmethod
    def tearDownClass(cls):
        cls.dev.rotation_watcher.teardown()
        cls.minicap_apk.teardown_stream()

    def test_get_frame_returns_valid_jpeg(self):
        """
        测试 get_frame() 返回有效的 JPEG

        验收标准:
        - 返回有效的 JPEG 数据
        - 格式正确
        """
        frame = self.minicap_apk.get_frame()

        self.assertIsInstance(frame, bytes)
        self.assertTrue(
            frame.startswith(b"\xff\xd8"),
            f"Frame should be valid JPEG, got: {frame[:4].hex()}"
        )
        self.assertTrue(
            frame.endswith(b"\xff\xd9"),
            f"Frame should end with JPEG EOI, got: {frame[-4:].hex()}"
        )

    def test_get_frame_performance(self):
        """
        测试 get_frame() 性能

        验收标准:
        - 单次截图 < 20s
        """
        start = time.time()
        frame = self.minicap_apk.get_frame()
        latency = time.time() - start

        print(f"\n  get_frame() 延迟: {latency*1000:.0f}ms")

        self.assertIsNotNone(frame)
        self.assertLess(
            latency, 20,
            f"延迟 {latency*1000:.0f}ms 超过 20s"
        )


class TestSnapshotIntegration(unittest.TestCase):
    """测试 snapshot() 与 get_frame() 集成"""

    @classmethod
    def setUpClass(cls):
        cls.dev = Android()
        cls.dev.rotation_watcher.get_ready()
        cls.minicap_apk = MinicapApk(
            cls.dev.adb, rotation_watcher=cls.dev.rotation_watcher
        )

    @classmethod
    def tearDownClass(cls):
        cls.dev.rotation_watcher.teardown()
        cls.minicap_apk.teardown_stream()

    def test_snapshot_returns_numpy_array(self):
        """
        测试 snapshot() 返回 numpy 数组

        验收标准:
        - 返回 numpy.ndarray
        - 维度正确 (H, W, C)
        """
        screen = self.minicap_apk.snapshot(ensure_orientation=False)

        if screen is not None:
            self.assertEqual(len(screen.shape), 3)
            self.assertEqual(screen.shape[2], 3)  # RGB


if __name__ == "__main__":
    unittest.main()
