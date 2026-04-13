# -*- coding: utf-8 -*-
import os
import select
import socket
import struct
import threading
import time
import traceback
from functools import partial, wraps

import six

from airtest import aircv
from airtest.core.android.cap_methods.base_cap import BaseCap
from airtest.core.error import ScreenError
from airtest.utils.logger import get_logger
from airtest.utils.nbsp import NonBlockingStreamReader
from airtest.utils.safesocket import SafeSocket
from airtest.utils.snippet import kill_proc, on_method_ready, ready_method, reg_cleanup
from airtest.utils.threadsafe import threadsafe_generator

LOGGING = get_logger(__name__)


def retry_when_socket_error(func, max_retries=3):
    @wraps(func)
    def wrapper(inst, *args, **kwargs):
        for attempt in range(max_retries):
            try:
                return func(inst, *args, **kwargs)
            except socket.error:
                LOGGING.warning("socket error on attempt %d/%d, retrying..." % (attempt + 1, max_retries))
                inst.frame_gen = None
                if attempt < max_retries - 1:
                    time.sleep(0.5 * (2 ** attempt))
        return func(inst, *args, **kwargs)

    return wrapper


class MinicapApk(BaseCap):
    """minicap-debug.apk based screenshot method, compatible with minicap options.

    reference https://github.com/openstf/minicap
    """

    VERSION = 5
    RECVTIMEOUT = (
        5  # 5s timeout
    )
    CMD = "CLASSPATH=/data/local/tmp/minicap-debug.apk app_process /system/bin io.devicefarmer.minicap.Main"
    APK_PATH = "/data/local/tmp/minicap-debug.apk"

    def __init__(
        self,
        adb,
        projection=None,
        rotation_watcher=None,
        display_id=None,
        ori_function=None,
    ):
        """
        :param adb: adb instance of android device
        :param projection: projection, default is None. If `None`, physical display size is used
        """
        super(MinicapApk, self).__init__(adb=adb)
        self.projection = projection
        self.display_id = display_id
        self.ori_function = ori_function or self.adb.get_display_info
        self.frame_gen = None
        self.stream_lock = threading.Lock()
        self.quirk_flag = 0
        self._stream_rotation = None
        self._update_rotation_event = threading.Event()
        if rotation_watcher:
            # Minicap needs to be reconnected when switching between landscape and portrait
            # minicap需要在横竖屏转换时，重新连接
            rotation_watcher.reg_callback(lambda x: self.update_rotation(x * 90))
        self.cleanup_func = []
        # Force cleanup on exit
        reg_cleanup(self.teardown_stream)

    @ready_method
    def install_or_upgrade(self):
        """
        Install or upgrade minicap-debug.apk

        Returns:
            None

        """
        if self.adb.exists_file(self.APK_PATH):
            LOGGING.debug("minicap-debug.apk already exists, skip installation")
            return
        else:
            LOGGING.debug("install minicap-debug.apk")
        self.install()

    def uninstall(self):
        """
        Uninstall minicap-debug.apk

        Returns:
            None

        """
        try:
            self.adb.raw_shell("rm %s" % self.APK_PATH)
        except Exception as e:
            # AdbError: No such file or directory
            LOGGING.warning(e)

    def install(self):
        """
        Install minicap-debug.apk

        Returns:
            None

        """
        from airtest.core.android.constant import STATICPATH

        local_apk_path = os.path.join(STATICPATH, "apks", "minicap-debug.apk")

        if not os.path.exists(local_apk_path):
            raise RuntimeError("minicap-debug.apk not found at %s" % local_apk_path)

        self.adb.push(local_apk_path, self.APK_PATH)
        self.adb.shell("chmod 755 %s" % self.APK_PATH)
        LOGGING.info("minicap-debug.apk installation finished")

    @on_method_ready("install_or_upgrade")
    def get_frame(self, projection=None):
        """
        Get a single frame using direct socket connection.
        Simplified approach similar to reference test_lazy_mode.py:
        1. Setup server
        2. Connect socket
        3. Send request
        4. Receive frame
        5. Cleanup
        """
        if self._update_rotation_event.is_set():
            LOGGING.debug("get_frame: rotation update, teardown")
            self.teardown_stream()
            self._update_rotation_event.clear()
        
        try:
            self._cleanup_minicap()
            proc, nbsp, localport = self._setup_stream_server(lazy=True)
            
            # Use plain socket instead of SafeSocket to avoid buffering issues
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(30)
            s.connect((self.adb.host, localport))
            LOGGING.debug("get_frame: connected to localhost:%d", localport)
            
            banner = s.recv(24)
            LOGGING.debug("get_frame: received banner: %d bytes, first 10 bytes: %s", len(banner), banner[:10])
            if len(banner) != 24:
                raise ScreenError("Failed to receive banner: got %d bytes" % len(banner))
            LOGGING.debug("get_frame: received banner, 24 bytes")
            
            # Parse banner and determine request pattern
            try:
                banner_data = struct.unpack("<2B5I2B", banner)
                LOGGING.debug("get_frame: banner parsed - version=%d, screen=%dx%d, target=%dx%d, ori=%d, quirk=%d",
                              banner_data[0], banner_data[3], banner_data[4], 
                              banner_data[5], banner_data[6], banner_data[7], banner_data[8])
                ori = banner_data[7]
            except Exception as e:
                LOGGING.debug("get_frame: failed to parse banner: %s", e)
                ori = 1  # Default to non-zero rotation
            
            # Optimized request pattern based on rotation
            use_hybrid = (ori != 0)
            if use_hybrid:
                # For rotation=90, need hybrid approach
                LOGGING.debug("get_frame: sending b'1' to wake up server")
                s.send(b"1")
                time.sleep(0.5)
                LOGGING.debug("get_frame: sending b'\\x00' to request frame")
                s.send(b'\x00')
                time.sleep(0.5)
            else:
                # For rotation=0, only b"1" is needed
                LOGGING.debug("get_frame: sending b'1' to request frame")
                s.send(b"1")
                time.sleep(0.5)
            
            # Try reading - if fails, try sending again (like stream version does in loop)
            LOGGING.debug("get_frame: attempting to receive frame header...")
            
            # Check if socket is still usable
            if s._closed:
                raise ScreenError("Socket was closed unexpectedly")
            
            # Set timeout for receive
            s.settimeout(15)
            LOGGING.debug("get_frame: waiting for frame header...")
            
            # Receive frame header (4 bytes)
            s.settimeout(15)
            header = s.recv(4)
            LOGGING.debug("get_frame: received header: %d bytes", len(header))
            if len(header) != 4:
                raise ScreenError("Failed to receive frame header: got %d bytes" % len(header))
            
            frame_size = struct.unpack("<I", header)[0]
            LOGGING.debug("get_frame: frame header received, size=%d", frame_size)
            if frame_size == 0:
                raise ScreenError("Invalid frame size: 0")
            
            # Receive frame data with chunked reading for large frames
            LOGGING.debug("get_frame: receiving frame data (%d bytes)...", frame_size)
            frame_data = b""
            remaining = frame_size
            chunk_size = 65536
            while remaining > 0:
                chunk = s.recv(min(remaining, chunk_size))
                if not chunk:
                    break
                frame_data += chunk
                remaining -= len(chunk)
            
            if len(frame_data) != frame_size:
                raise ScreenError("Failed to receive frame data: expected %d, got %d" % (frame_size, len(frame_data)))
            
            LOGGING.debug("get_frame: received frame, size=%d" % len(frame_data))
            
            s.close()
            nbsp.kill()
            kill_proc(proc)
            self.adb.remove_forward("tcp:%s" % localport)
            
            return frame_data
            
        except Exception as e:
            LOGGING.debug("get_frame failed: %s", e)
            self.teardown_stream()
            raise ScreenError("minicap_apk get_frame failed: %s" % e)

    def _get_params(self, projection=None):
        """
        Get the minicap origin parameters and count the projection

        Returns:
            physical display size (width, height), counted projection (width, height) and real display orientation

        """
        display_info = self.ori_function()
        real_width = display_info["width"]
        real_height = display_info["height"]
        real_rotation = display_info["rotation"]
        # 优先去传入的projection
        projection = projection or self.projection
        if projection:
            proj_width, proj_height = projection
        else:
            proj_width, proj_height = real_width, real_height

        if self.quirk_flag & 2 and real_rotation in (90, 270):
            params = real_height, real_width, proj_height, proj_width, 0
        else:
            params = real_width, real_height, proj_width, proj_height, real_rotation

        return (params, display_info)

    @on_method_ready("install_or_upgrade")
    def get_stream(self, lazy=True):
        """
        Get stream, it uses `adb forward`and socket communication. Use minicap ``lazy``mode (provided by gzmaruijie)
        for long connections - returns one latest frame from the server


        Args:
            lazy: True or False

        Returns:

        """
        gen = self._get_stream(lazy)

        # if quirk error, restart server and client once
        stopped = next(gen)

        if stopped:
            try:
                next(gen)
            except StopIteration:
                pass
            gen = self._get_stream(lazy)
            next(gen)

        return gen

    @threadsafe_generator
    @on_method_ready("install_or_upgrade")
    def _get_stream(self, lazy=True):
        """
        Setup socket connection for lazy mode.
        Simple flow: send request -> receive frame -> repeat
        """
        self._cleanup_minicap()
        proc, nbsp, localport = self._setup_stream_server(lazy=lazy)
        
        s = SafeSocket()
        s.sock.settimeout(15)  # 15s timeout to prevent hang
        s.connect((self.adb.host, localport))
        
        # Receive banner (24 bytes)
        t = s.recv(24)
        if len(t) != 24:
            raise ScreenError("Failed to receive banner")
        
        # Parse header to get orientation and quirk
        global_headers = struct.unpack("<2B5I2B", t)
        LOGGING.debug(global_headers)
        ori, self.quirk_flag = global_headers[-2:]
        
        # Optimization: For rotation=0, only b"1" is needed
        # For rotation=90, need hybrid approach for first frame
        self._use_hybrid_request = (ori != 0)
        LOGGING.debug("lazy mode: ori=%d, use_hybrid=%s", ori, self._use_hybrid_request)
        
        # Check quirk
        if self.quirk_flag & 2 and ori in (1, 3):
            LOGGING.debug("quirk_flag found, going to resetup")
            stopping = True
        else:
            stopping = False
        
        # Register cleanup
        self.cleanup_func.append(s.close)
        self.cleanup_func.append(nbsp.kill)
        self.cleanup_func.append(partial(kill_proc, proc))
        self.cleanup_func.append(partial(self.adb.remove_forward, "tcp:%s" % localport))
        yield stopping
        
        # In lazy mode, send initial request after yield
        # Optimized: For rotation=0, only b"1" is needed; for rotation=90, need hybrid
        if lazy:
            if self._use_hybrid_request:
                LOGGING.debug("lazy mode: initial request (hybrid for ori=%d)", ori)
                s.sock.send(b"1")
                time.sleep(0.5)
                s.sock.send(b'\x00')
                time.sleep(0.5)
            else:
                LOGGING.debug("lazy mode: initial request (b'1' only for ori=0)")
                s.sock.send(b"1")
                time.sleep(0.5)
        
        # Main loop: send request -> receive frame
        try:
            while True:
                if lazy:
                    # Optimized: For rotation=0, only b"1" is needed after first frame
                    if self._use_hybrid_request:
                        s.sock.send(b"1")
                        time.sleep(0.5)
                        s.sock.send(b'\x00')
                        time.sleep(0.5)
                    else:
                        s.sock.send(b"1")
                        time.sleep(0.5)
                    LOGGING.debug("lazy mode: sent request")
                
                # Receive frame header (4 bytes) with timeout
                s.sock.settimeout(15)
                header = s.recv(4)
                if len(header) != 4:
                    LOGGING.error("Failed to receive frame header")
                    break
                
                frame_size = struct.unpack("<I", header)[0]
                if frame_size == 0:
                    LOGGING.error("Invalid frame size: 0")
                    break
                
                # Receive frame data
                frame_data = s.recv(frame_size)
                if len(frame_data) != frame_size:
                    LOGGING.error("Failed to receive frame data")
                    break
                
                LOGGING.debug("lazy mode: received frame, size=%d" % len(frame_data))
                yield frame_data
                
        except Exception as e:
            LOGGING.debug("Stream error: %s", e)
        finally:
            LOGGING.debug("minicap stream ends")
            self._cleanup()

    def _setup_stream_server(self, lazy=True):
        """
        Setup minicap-debug.apk process on device

        Args:
            lazy: parameter `-l` is used when True

        Returns:
            adb shell process, non-blocking stream reader and local port

        """
        localport, deviceport = self.adb.setup_forward(
            "localabstract:minicap_apk_{}".format
        )
        deviceport = deviceport[len("localabstract:") :]
        other_opt = "-l" if lazy else "-r 30"  # lazy mode or frame rate
        params, display_info = self._get_params()
        if self.display_id:
            proc = self.adb.start_shell(
                "%s -d %s -n '%s' -P %dx%d@%dx%d/%d %s 2>&1"
                % tuple(
                    [self.CMD, self.display_id, deviceport] + list(params) + [other_opt]
                ),
            )
        else:
            proc = self.adb.start_shell(
                "%s -n '%s' -P %dx%d@%dx%d/%d %s 2>&1"
                % tuple([self.CMD, deviceport] + list(params) + [other_opt]),
            )
        nbsp = NonBlockingStreamReader(
            proc.stdout, print_output=True, name="minicap_apk_server", auto_kill=True
        )
        
        # Wait for server to start, with timeout
        start_time = time.time()
        max_wait = 10  # 10 seconds max
        while time.time() - start_time < max_wait:
            line = nbsp.readline(timeout=1.0)
            if line is None:
                if proc.poll() is not None:
                    raise RuntimeError("minicap-apk server quit immediately")
                continue
            if b"Listening on socket : minicap_apk_" in line:
                break
        else:
            kill_proc(proc)
            raise RuntimeError("minicap-apk server setup timeout")

        if proc.poll() is not None:
            # minicap server setup error, may be already setup by others
            # subprocess exit immediately
            kill_proc(proc)
            raise RuntimeError("minicap-apk server quit immediately")

        self._stream_rotation = int(display_info["rotation"])
        return proc, nbsp, localport

    @retry_when_socket_error
    def get_frame_from_stream(self):
        """
        Get one frame from minicap stream

        Returns:
            frame

        """
        if self._update_rotation_event.is_set():
            LOGGING.debug("do update rotation")
            self.teardown_stream()
            self._update_rotation_event.clear()
        if self.frame_gen is None:
            self.frame_gen = self.get_stream(True)
            self._last_request_time = time.time()
        
        # In lazy mode, ensure minimum interval between requests
        # This allows the server to capture a new frame
        current_time = time.time()
        elapsed = current_time - getattr(self, '_last_request_time', current_time)
        if elapsed < 0.1:
            wait_time = 0.1 - elapsed
            LOGGING.debug("lazy mode: waiting %.2fs before next request" % wait_time)
            time.sleep(wait_time)
        
        frame = six.next(self.frame_gen)
        
        if frame is None:
            LOGGING.debug("received None frame, reconnecting")
            self.frame_gen = None
            return self.get_frame_from_stream()
        
        self._last_request_time = time.time()
        return frame

    def snapshot(self, ensure_orientation=True, projection=None):
        """

        Args:
            ensure_orientation: True or False whether to keep the orientation same as display
            projection: the size of the desired projection, (width, height)

        Returns:

        """
        if projection:
            # minicap模式在单张截图时，可以传入projection参数来强制指定图片大小，如手机分辨率(width, height)
            screen = self.get_frame(projection=projection)
            try:
                screen = aircv.utils.string_2_img(screen)
            except Exception:
                # may be black/locked screen or other reason, print exc for debugging
                traceback.print_exc()
                return None
            return screen
        else:
            return super(MinicapApk, self).snapshot()

    def update_rotation(self, rotation):
        """
        Update rotation and reset the backend stream generator

        Args:
            rotation: rotation input

        Returns:
            None

        """
        LOGGING.debug("update_rotation: %s" % rotation)
        self._update_rotation_event.set()

    def _cleanup_minicap(self):
        """
        Clean up the minicap process whose status is __skb_wait_for_more_packets or futex_wait_queue_me
        清理状态为__skb_wait_for_more_packets, futex_wait_queue_me的minicap进程

        Returns:

        """
        TASK_INTERRUPTIBLE1 = "__skb_wait_for_more_packets"
        TASK_INTERRUPTIBLE2 = "futex_wait_queue_me"

        shell_output = ""
        try:
            shell_output = self.adb.shell("ps -A| grep io.devicefarmer.minicap")
        except Exception as e:
            LOGGING.debug("ps -A failed: %s, trying ps without -A", e)
            try:
                shell_output = self.adb.shell("ps| grep io.devicefarmer.minicap")
            except Exception as e:
                LOGGING.debug("ps also failed: %s", e)
                pass

        if not shell_output or len(shell_output) == 0:
            return
        for line in shell_output.split("\r\n"):
            if TASK_INTERRUPTIBLE1 in line or TASK_INTERRUPTIBLE2 in line:
                try:
                    pid = line.split()[1]
                    self.adb.shell("kill %s" % pid)
                except Exception as e:
                    LOGGING.debug("Failed to kill pid: %s", e)

    def _cleanup(self):
        """
        Cleanup minicap process and stream reader

        主动将minicap建立的各个连接关闭
        与snippet.py中的CLEANUP_CALLS功能相同，但是允许主动调用，避免异常退出时有遗漏进程没清理干净

        Returns:

        """
        for func in self.cleanup_func:
            try:
                func()
            except Exception as e:
                LOGGING.debug("Cleanup func failed: %s", e)
        self.cleanup_func = []

    def teardown_stream(self):
        """
        End the stream

        Returns:
            None

        """
        # clean up established connections
        self._cleanup()
        if not self.frame_gen:
            return
        try:
            self.frame_gen.send(1)
        except (TypeError, StopIteration):
            # TypeError: can't send non-None value to a just-started generator
            pass
        else:
            LOGGING.warn("%s tear down failed" % self.frame_gen)
        self.frame_gen = None
