# -*- coding: utf-8 -*-
"""Common base class for minicap and minicap_apk screenshot methods.

Holds the stream lifecycle pieces that are shared verbatim by
`airtest.core.android.cap_methods.minicap.Minicap` and
`airtest.core.android.cap_methods.minicap_apk.MinicapApk`
(rotation event handling, quirk resetup, params/snapshot/teardown helpers),
so that each subclass only keeps its own protocol differences.
"""
import socket
import threading
import time
import traceback
from functools import wraps

from airtest import aircv
from airtest.core.android.cap_methods.base_cap import BaseCap
from airtest.utils.logger import get_logger
from airtest.utils.snippet import on_method_ready, reg_cleanup

LOGGING = get_logger(__name__)


def retry_when_socket_error(func):
    @wraps(func)
    def wrapper(inst, *args, **kwargs):
        try:
            return func(inst, *args, **kwargs)
        except socket.error:
            inst.frame_gen = None
            return func(inst, *args, **kwargs)

    return wrapper


def retry_when_socket_error_with_backoff(func, max_retries=3):
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


class MinicapBase(BaseCap):
    """Base class of the minicap based screenshot methods, holding the shared
    stream lifecycle (setup/teardown, rotation events, quirk resetup).

    Subclasses must provide `CMD`, `RECVTIMEOUT`, `install_or_upgrade`, their
    own `_get_stream` protocol loop, `_setup_stream_server` and `_cleanup`.
    """

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
        super(MinicapBase, self).__init__(adb=adb)
        self.projection = projection
        self.display_id = display_id
        self.ori_function = ori_function or self.adb.get_display_info
        self.frame_gen = None
        self.quirk_flag = 0
        self._update_rotation_event = threading.Event()
        if rotation_watcher:
            # Minicap needs to be reconnected when switching between landscape and portrait
            # minicap需要在横竖屏转换时，重新连接
            rotation_watcher.reg_callback(lambda x: self.update_rotation(x * 90))
        self.cleanup_func = []
        # Force cleanup on exit
        reg_cleanup(self.teardown_stream)

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
    def get_stream(self, lazy=True, projection=None):
        """
        Get stream, it uses `adb forward`and socket communication. Use minicap ``lazy``mode (provided by gzmaruijie)
        for long connections - returns one latest frame from the server


        Args:
            lazy: True or False
            projection: projection used to setup the stream server, default is None which means using self.projection

        Returns:

        """
        gen = self._get_stream(lazy, projection)

        # if quirk error, restart server and client once
        stopped = next(gen)

        if stopped:
            try:
                next(gen)
            except StopIteration:
                pass
            gen = self._get_stream(lazy, projection)
            next(gen)

        return gen

    def _start_stream_proc(self, deviceport, params, other_opt):
        """
        Start the minicap server process on device

        Args:
            deviceport: device abstract socket name the server listens on
            params: minicap `-P` parameters counted by `_get_params`
            other_opt: additional command line options

        Returns:
            adb shell process

        """
        if self.display_id:
            return self.adb.start_shell(
                "%s -d %s -n '%s' -P %dx%d@%dx%d/%d %s 2>&1"
                % tuple(
                    [self.CMD, self.display_id, deviceport] + list(params) + [other_opt]
                ),
            )
        return self.adb.start_shell(
            "%s -n '%s' -P %dx%d@%dx%d/%d %s 2>&1"
            % tuple([self.CMD, deviceport] + list(params) + [other_opt]),
        )

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
            return super(MinicapBase, self).snapshot()

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

    def _reset_stream_state(self):
        """
        Hook for subclasses to reset their own stream state on teardown

        Returns:
            None

        """
        pass

    def teardown_stream(self):
        """
        End the stream

        Returns:
            None

        """
        # clean up established connections
        self._cleanup()
        self._reset_stream_state()
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
