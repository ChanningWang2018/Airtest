# -*- coding: utf-8 -*-
"""
单独测试 JAVACAP 截图方法
"""

import pytest
from airtest.core.android.adb import ADB
from airtest.core.android.constant import CAP_METHOD
from airtest.core.api import init_device
from airtest.core.android.cap_methods.javacap import Javacap
import time


class TestJavacapOnly:
    """单独测试JAVACAP"""

    @pytest.fixture(scope="class")
    def adb(self):
        adb = ADB()
        devices = adb.devices()
        if not devices:
            pytest.skip("No device found")
        serialno = devices[0][0]
        adb.serialno = serialno
        print(f"\nDevice: {serialno}")
        return adb, serialno

    def test_javacap_with_manual_install(self, adb):
        """手动安装Yosemite.apk后测试Javacap"""
        adb_instance, serialno = adb
        
        print("\n=== 1. 手动推送并安装Yosemite.apk ===")
        local_apk = r"E:\projects\airtest\Airtest\airtest\core\android\static\apks\Yosemite.apk"
        
        # 推送到 /data/local/tmp/
        device_path = adb_instance.push(local_apk, "/data/local/tmp/Yosemite.apk")
        print(f"Pushed to: {device_path}")
        
        # 安装
        try:
            adb_instance.shell(["pm", "install", "-t", "-r", device_path])
            print("Install success!")
        except Exception as e:
            print(f"Install failed: {e}")
        
        # 检查安装结果
        try:
            pkg_path = adb_instance.path_app("com.netease.nie.yosemite")
            print(f"APK installed at: {pkg_path}")
        except Exception as e:
            print(f"Package not found: {e}")
        
        print("\n=== 2. 创建Javacap实例 ===")
        javacap = Javacap(adb_instance)
        print(f"Javacap created: {type(javacap)}")
        
        print("\n=== 3. 测试服务器启动 ===")
        try:
            proc, nbsp, localport = javacap._setup_stream_server()
            print(f"Server setup success: port={localport}")
        except Exception as e:
            print(f"Server setup failed: {e}")
            raise

        print("\n=== 4. 尝试接收banner ===")
        from airtest.utils.safesocket import SafeSocket
        s = SafeSocket()
        try:
            s.connect((adb_instance.host, localport))
            print("Socket connected")
            t = s.recv(24)
            print(f"Received banner: {len(t)} bytes")
        except Exception as e:
            print(f"Receive banner failed: {e}")
            s.close()
            nbsp.kill()
            raise

        print("\n=== 5. 清理 ===")
        s.close()
        nbsp.kill()

        print("\n=== 测试完成 ===")


if __name__ == '__main__':
    pytest.main([__file__, '-v', '-s'])