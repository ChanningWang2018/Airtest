# -*- coding: utf-8 -*-
import os
import re
import struct
import threading
import six
from functools import partial
from airtest.core.android.constant import STFLIB
from airtest.utils.logger import get_logger
from airtest.utils.nbsp import NonBlockingStreamReader
from airtest.utils.safesocket import SafeSocket
from airtest.utils.snippet import on_method_ready, ready_method, kill_proc
from airtest.utils.threadsafe import threadsafe_generator
from airtest.core.android.cap_methods.minicap_base import (
    MinicapBase,
    retry_when_socket_error,
)
from airtest.core.error import ScreenError

LOGGING = get_logger(__name__)


class Minicap(MinicapBase):
    """super fast android screenshot method from stf minicap.

    reference https://github.com/openstf/minicap
    """

    VERSION = 5
    RECVTIMEOUT = (
        3  # default value is None, but the version above 1.2.7 is changed to 3s
    )
    CMD = "LD_LIBRARY_PATH=/data/local/tmp /data/local/tmp/minicap"

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
        super(Minicap, self).__init__(
            adb=adb,
            projection=projection,
            rotation_watcher=rotation_watcher,
            display_id=display_id,
            ori_function=ori_function,
        )
        self.stream_lock = threading.Lock()
        self._stream_rotation = None

    @ready_method
    def install_or_upgrade(self):
        """
        Install or upgrade minicap

        Returns:
            None

        """
        if self.adb.exists_file("/data/local/tmp/minicap") and self.adb.exists_file(
            "/data/local/tmp/minicap.so"
        ):
            try:
                output = self.adb.raw_shell("%s -v 2>&1" % self.CMD)
            except Exception as err:
                LOGGING.error(str(err))
                version = -1
            else:
                LOGGING.debug(output.strip())
                m = re.match("version:(\d)", output)
                if m:
                    version = int(m.group(1))
                else:
                    version = -1
            if version >= self.VERSION:
                LOGGING.debug("skip install minicap")
                return
            else:
                LOGGING.debug(
                    "upgrade minicap to lastest version: %s->%s"
                    % (version, self.VERSION)
                )
                self.uninstall()
        else:
            LOGGING.debug("install minicap")
        self.install()

    def uninstall(self):
        """
        Uninstall minicap

        Returns:
            None

        """
        try:
            self.adb.raw_shell("rm -r /data/local/tmp/minicap*")
        except Exception as e:
            # AdbError: No such file or directory
            LOGGING.warning(e)

    def install(self):
        """
        Install minicap

        Reference: https://github.com/openstf/minicap/blob/master/run.sh

        Returns:
            None

        """
        abi = self.adb.getprop("ro.product.cpu.abi")
        pre = self.adb.getprop("ro.build.version.preview_sdk")
        rel = self.adb.getprop("ro.build.version.release")
        sdk = self.adb.sdk_version

        if pre.isdigit() and int(pre) > 0:
            sdk += 1

        if sdk >= 16:
            binfile = "minicap"
        else:
            binfile = "minicap-nopie"

        device_dir = "/data/local/tmp"

        path = os.path.join(STFLIB, abi, binfile)
        self.adb.push(path, "%s/minicap" % device_dir)
        print(path)
        self.adb.shell("chmod 755 %s/minicap" % device_dir)

        pattern = os.path.join(
            STFLIB, "minicap-shared/aosp/libs/android-%s/%s/minicap.so"
        )
        path = pattern % (sdk, abi)
        if not os.path.isfile(path):
            path = pattern % (rel, abi)

        self.adb.push(path, "%s/minicap.so" % device_dir)
        self.adb.shell("chmod 755 %s/minicap.so" % device_dir)
        LOGGING.info("minicap installation finished")

    @on_method_ready("install_or_upgrade")
    def get_frame(self, projection=None):
        """
        Get the single frame from minicap -s, this method slower than `get_frames`
            1. shell cmd
            1. remove log info
            1. \r\r\n -> \n ...

        Args:
            projection: screenshot projection, default is None which means using self.projection

        Returns:
            jpg data

        """
        params, display_info = self._get_params(projection)
        if self.display_id:
            raw_data = self.adb.raw_shell(
                self.CMD
                + " -d "
                + str(self.display_id)
                + " -n 'airtest_minicap' -P %dx%d@%dx%d/%d -s" % params,
                ensure_unicode=False,
            )
        else:
            raw_data = self.adb.raw_shell(
                self.CMD + " -n 'airtest_minicap' -P %dx%d@%dx%d/%d -s" % params,
                ensure_unicode=False,
            )
        jpg_data = raw_data.split(b"for JPG encoder" + self.adb.line_breaker)[-1]
        jpg_data = jpg_data.replace(self.adb.line_breaker, b"\n")
        if jpg_data.startswith(b"\xff\xd8") and jpg_data.endswith(b"\xff\xd9"):
            return jpg_data
        else:
            raise ScreenError("invalid jpg format")

    @threadsafe_generator
    @on_method_ready("install_or_upgrade")
    def _get_stream(self, lazy=True, projection=None):
        # `projection` is accepted for compatibility with the base class
        # `get_stream` signature; the minicap stream projection is fixed at
        # server setup by ``self.projection`` (per-frame projections are
        # handled by the one-shot `get_frame`)
        self._cleanup_minicap()
        proc, nbsp, localport = self._setup_stream_server(lazy=lazy)
        s = SafeSocket()
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
        self.cleanup_func.append(s.close)
        self.cleanup_func.append(nbsp.kill)
        self.cleanup_func.append(partial(kill_proc, proc))
        self.cleanup_func.append(partial(self.adb.remove_forward, "tcp:%s" % localport))
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
                # recv timeout, if not frame updated, maybe screen locked
                stopping = yield None
            else:
                frame_size = struct.unpack("<I", header)[0]
                if self.RECVTIMEOUT is not None:
                    frame_data = s.recv_with_timeout(frame_size, self.RECVTIMEOUT)
                else:
                    frame_data = s.recv(frame_size)
                stopping = yield frame_data

        LOGGING.debug("minicap stream ends")
        # teardown stream() cannot be called directly because the connection may be rebuilt multiple times
        # while the screen is rotated, and self.frame_gen gets stuck
        self._cleanup()

    def _setup_stream_server(self, lazy=False):
        """
        Setup minicap process on device

        Args:
            lazy: parameter `-l` is used when True

        Returns:
            adb shell process, non-blocking stream reader and local port

        """
        localport, deviceport = self.adb.setup_forward(
            "localabstract:minicap_{}".format
        )
        deviceport = deviceport[len("localabstract:") :]
        other_opt = "-l" if lazy else ""
        params, display_info = self._get_params()
        proc = self._start_stream_proc(deviceport, params, other_opt)
        nbsp = NonBlockingStreamReader(
            proc.stdout, print_output=True, name="minicap_server", auto_kill=True
        )
        while True:
            line = nbsp.readline(timeout=5.0)
            if line is None:
                kill_proc(proc)
                raise RuntimeError("minicap server setup timeout")
            if b"Server start" in line:
                break

        if proc.poll() is not None:
            # minicap server setup error, may be already setup by others
            # subprocess exit immediately
            kill_proc(proc)
            raise RuntimeError("minicap server quit immediately")

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
            self.frame_gen = self.get_stream()
        return six.next(self.frame_gen)

    def _cleanup_minicap(self):
        """
        Clean up the minicap process whose status is __skb_wait_for_more_packets or futex_wait_queue_me
        清理状态为__skb_wait_for_more_packets, futex_wait_queue_me的minicap进程

        Returns:

        """
        # 卡住的进程状态
        TASK_INTERRUPTIBLE1 = "__skb_wait_for_more_packets"
        TASK_INTERRUPTIBLE2 = "futex_wait_queue_me"

        shell_output = ""
        try:
            shell_output = self.adb.shell("ps -A| grep minicap")
        except:
            try:
                shell_output = self.adb.shell("ps| grep minicap")
            except:
                pass

        if len(shell_output) == 0:
            return
        for line in shell_output.split("\r\n"):
            if TASK_INTERRUPTIBLE1 in line or TASK_INTERRUPTIBLE2 in line:
                pid = line.split()[1]
                try:
                    self.adb.shell("kill %s" % pid)
                except:
                    pass

    def _cleanup(self):
        """
        Cleanup minicap process and stream reader

        主动将minicap建立的各个连接关闭
        与snippet.py中的CLEANUP_CALLS功能相同，但是允许主动调用，避免异常退出时有遗漏进程没清理干净

        Returns:

        """
        for func in self.cleanup_func:
            func()
        self.cleanup_func = []
