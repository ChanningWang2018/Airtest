# -*- coding: utf-8 -*-
import hashlib
import os
import re
import struct
import time
from functools import partial

import six

from airtest.core.android.cap_methods.minicap_base import (
    MinicapBase,
    retry_when_socket_error_with_backoff as retry_when_socket_error,
)
from airtest.core.error import ScreenError
from airtest.utils.logger import get_logger
from airtest.utils.nbsp import NonBlockingStreamReader
from airtest.utils.safesocket import SafeSocket
from airtest.utils.snippet import kill_proc, on_method_ready, ready_method
from airtest.utils.threadsafe import threadsafe_generator

LOGGING = get_logger(__name__)


class MinicapApk(MinicapBase):
    """minicap-debug.apk based screenshot method, compatible with minicap options.

    reference https://github.com/ChanningWang2018/minicap
    """

    RECVTIMEOUT = (
        3  # server waits at most 2s for a requested frame, so client timeout is 3s
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
        super(MinicapApk, self).__init__(
            adb=adb,
            projection=projection,
            rotation_watcher=rotation_watcher,
            display_id=display_id,
            ori_function=ori_function,
        )
        self._stream_projection = None

    @ready_method
    def install_or_upgrade(self):
        """
        Install or upgrade minicap-debug.apk

        Returns:
            None

        """
        if self._apk_is_up_to_date():
            LOGGING.debug("minicap-debug.apk already up to date, skip installation")
            return
        LOGGING.debug("install minicap-debug.apk")
        self.install()

    def _local_apk_path(self):
        """
        Get the local minicap-debug.apk path shipped with airtest

        Returns:
            local apk file path

        """
        from airtest.core.android.constant import STATICPATH

        return os.path.join(STATICPATH, "apks", "minicap-debug.apk")

    def _apk_is_up_to_date(self):
        """
        Check whether the apk on device is the same as the local one by md5.

        When the `md5sum` command is not available on device, fall back to
        the exists-file check.

        Returns:
            True if the device apk is up to date

        """
        if not self.adb.exists_file(self.APK_PATH):
            return False
        try:
            md5 = hashlib.md5()
            with open(self._local_apk_path(), "rb") as f:
                for chunk in iter(lambda: f.read(8192), b""):
                    md5.update(chunk)
            local_md5 = md5.hexdigest()
            # md5sum output looks like: "<md5>  /data/local/tmp/minicap-debug.apk"
            output = self.adb.shell("md5sum %s" % self.APK_PATH)
            device_md5 = output.strip().split()[0]
        except Exception as e:
            LOGGING.debug("md5 check failed, fallback to exists check: %s", e)
            return True
        if not re.match(r"^[0-9a-fA-F]{32}$", device_md5):
            LOGGING.debug("unexpected md5sum output, fallback to exists check: %r" % output)
            return True
        if local_md5 == device_md5:
            return True
        LOGGING.info(
            "minicap-debug.apk md5 mismatch (local: %s, device: %s), upgrading"
            % (local_md5, device_md5)
        )
        return False

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
        local_apk_path = self._local_apk_path()

        if not os.path.exists(local_apk_path):
            raise RuntimeError("minicap-debug.apk not found at %s" % local_apk_path)

        self.adb.push(local_apk_path, self.APK_PATH)
        self.adb.shell("chmod 755 %s" % self.APK_PATH)
        LOGGING.info("minicap-debug.apk installation finished")

    @on_method_ready("install_or_upgrade")
    def get_frame_via_stream(self, projection=None):
        """
        Get single frame using stream mode (reuses connection).

        This method uses the existing stream connection if available,
        or creates a new one. It's optimized for quick single captures.

        Args:
            projection: screenshot projection, when it differs from the projection
                the current stream was built with, the stream is rebuilt with it

        Returns:
            bytes: JPEG image data

        Raises:
            ScreenError: If screenshot fails

        """
        effective_projection = projection or self.projection
        if tuple(effective_projection or ()) != tuple(self._stream_projection or ()):
            LOGGING.debug(
                "projection changed %s -> %s, rebuild stream"
                % (self._stream_projection, effective_projection)
            )
            self.teardown_stream()
        return self._fetch_stream_frame(projection=projection, raise_on_failure=True)

    @on_method_ready("install_or_upgrade")
    def get_frame(self, projection=None):
        """
        Get a single frame from minicap-debug.apk via the lazy stream.

        The stream connection is reused when possible, which is faster than
        starting a shell process for every capture.

        Args:
            projection: screenshot projection, default is None which means using self.projection

        Returns:
            jpg data

        Raises:
            ScreenError: If screenshot fails

        """
        return self.get_frame_via_stream(projection=projection)

    def _fetch_stream_frame(self, projection=None, raise_on_failure=True):
        """
        Get one frame from the lazy stream, shared by `get_frame_via_stream` and
        `get_frame_from_stream`. Handles rotation events and the frame generator
        lifecycle, with bounded retries when no frame is received (e.g. screen locked).

        Args:
            projection: projection used when the stream needs to be (re)built
            raise_on_failure: True to raise ScreenError when no frame is received,
                False to return None (recorder callers tolerate missing frames)

        Returns:
            frame data, or None when unavailable and `raise_on_failure` is False

        Raises:
            ScreenError: when no frame is received and `raise_on_failure` is True

        """
        max_attempts = 3  # 1 initial + 2 retries, the screen may be locked
        for attempt in range(max_attempts):
            if self._update_rotation_event.is_set():
                LOGGING.debug("do update rotation")
                self.teardown_stream()
                self._update_rotation_event.clear()
            frame = None
            if self.frame_gen is None:
                # get_stream consumes the first `yield stopping` internally,
                # no extra prime next() is needed
                self.frame_gen = self.get_stream(lazy=True, projection=projection)
            try:
                frame = six.next(self.frame_gen)
            except StopIteration:
                # generator ended (e.g. timeout due to rotation), rebuild on next attempt
                LOGGING.debug("minicap stream generator ended, will rebuild")
                self.frame_gen = None
            if frame is not None:
                return frame
            LOGGING.debug("no frame received (attempt %d/%d)" % (attempt + 1, max_attempts))
            if attempt < max_attempts - 1:
                time.sleep(0.5)
        if raise_on_failure:
            raise ScreenError("minicap_apk: no frame received from stream")
        return None

    @threadsafe_generator
    @on_method_ready("install_or_upgrade")
    def _get_stream(self, lazy=True, projection=None):
        """
        Setup socket connection for lazy mode.
        Simple flow: send request -> receive frame -> repeat
        """
        self._cleanup_minicap()
        proc, nbsp, localport = self._setup_stream_server(lazy=lazy, projection=projection)
        s = SafeSocket()
        # register cleanups before connecting, so that a connect/banner failure
        # cannot leak the server process, the socket or the port forward
        self.cleanup_func.append(s.close)
        self.cleanup_func.append(nbsp.kill)
        self.cleanup_func.append(partial(kill_proc, proc))
        self.cleanup_func.append(partial(self.adb.remove_forward, "tcp:%s" % localport))
        try:
            s.connect((self.adb.host, localport))
            t = s.recv(24)
            # minicap header
            global_headers = struct.unpack("<2B5I2B", t)
            LOGGING.debug(global_headers)
            # check quirk-bitflags, reference: https://github.com/openstf/minicap#quirk-bitflags
            ori, self.quirk_flag = global_headers[-2:]

            if self.quirk_flag & 2 and ori in (1, 3):
                # resetup
                LOGGING.debug("quirk_flag found, going to resetup")
                stopping = True
            else:
                stopping = False

            yield stopping

            while not stopping:
                if lazy:
                    s.send(b"1")
                # recv frame header, count frame_size
                if self.RECVTIMEOUT is not None:
                    # Some mobile phones may keep waiting for data when switching between horizontal and vertical screens,
                    # and the connection is not closed, resulting in a black screen
                    # Set the timeout to 3s(airtest>=1.2.7)
                    header = s.recv_with_timeout(4, self.RECVTIMEOUT)
                else:
                    header = s.recv(4)
                if header is None:
                    LOGGING.error("minicap header is None")
                    # recv timeout, check if due to rotation
                    if self._update_rotation_event.is_set():
                        LOGGING.debug("timeout due to rotation, teardown stream")
                        self._update_rotation_event.clear()
                        # Signal generator to stop and trigger reconnection
                        return
                    # recv timeout, if not frame updated, maybe screen locked
                    stopping = yield None
                else:
                    frame_size = struct.unpack("<I", header)[0]
                    if self.RECVTIMEOUT is not None:
                        frame_data = s.recv_with_timeout(frame_size, self.RECVTIMEOUT)
                    else:
                        frame_data = s.recv(frame_size)
                    stopping = yield frame_data
        finally:
            LOGGING.debug("minicap stream ends")
            self._cleanup()

    def _setup_stream_server(self, lazy=True, projection=None):
        """
        Setup minicap-debug.apk process on device

        Args:
            lazy: parameter `-l` is used when True
            projection: projection used to setup the server, default is None which means using self.projection

        Returns:
            adb shell process, non-blocking stream reader and local port

        """
        localport, deviceport = self.adb.setup_forward(
            "localabstract:minicap_apk_{}".format
        )
        deviceport = deviceport[len("localabstract:") :]
        other_opt = "-l" if lazy else "-r 30"  # lazy mode or frame rate
        params, display_info = self._get_params(projection)
        self._stream_projection = projection or self.projection
        proc = self._start_stream_proc(deviceport, params, other_opt)
        nbsp = NonBlockingStreamReader(
            proc.stdout, print_output=True, name="minicap_apk_server", auto_kill=True
        )

        # Wait for server to start, with timeout
        # Some devices/emulators need more time to start the server (up to 60s)
        start_time = time.time()
        max_wait = 60  # 60 seconds max for slow devices
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

        return proc, nbsp, localport

    @retry_when_socket_error
    def get_frame_from_stream(self):
        """
        Get one frame from minicap stream

        Returns:
            frame, None when no frame is received (e.g. screen locked)

        """
        return self._fetch_stream_frame(raise_on_failure=False)

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

    def _reset_stream_state(self):
        self._stream_projection = None
