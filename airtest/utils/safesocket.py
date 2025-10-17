# _*_ coding:UTF-8 _*_
import socket
import errno
import struct


class SafeSocket(object):
    """safe and exact recv & send"""

    def __init__(self, sock=None):
        if sock is None:
            self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        else:
            self.sock = sock
        self.buf = b""

    def __enter__(self):
        try:
            return self.sock.__enter__()
        except AttributeError:
            return self.sock

    def __exit__(self, exc_type, exc_val, exc_tb):
        try:
            return self.sock.__exit__(exc_type, exc_val, exc_tb)
        except AttributeError:
            self.sock.close()

    # PEP 3113 -- Removal of Tuple Parameter Unpacking
    # https://www.python.org/dev/peps/pep-3113/
    def connect(self, tuple_hp):
        host, port = tuple_hp
        self.sock.connect((host, port))

    def send(self, msg):
        totalsent = 0
        while totalsent < len(msg):
            sent = self.sock.send(msg[totalsent:])
            if sent == 0:
                raise socket.error("socket connection broken")
            totalsent += sent

    def recv(self, size):
        while len(self.buf) < size:
            trunk = self.sock.recv(min(size - len(self.buf), 4096))
            if trunk == b"":
                raise socket.error("socket connection broken")
            self.buf += trunk
        ret, self.buf = self.buf[:size], self.buf[size:]
        return ret

    def recv_with_timeout(self, size, timeout=2):
        self.sock.settimeout(timeout)
        try:
            ret = self.recv(size)
        except socket.timeout:
            ret = None
        finally:
            self.sock.settimeout(None)
        return ret

    def recv_nonblocking(self, size):
        self.sock.settimeout(0)
        try:
            ret = self.recv(size)
        except socket.error as e:
            # 10035 no data when nonblocking
            if e.args[0] == 10035:  # errno.EWOULDBLOCK: 尼玛errno似乎不一致
                ret = None
            # 10053 connection abort by client
            # 10054 connection reset by peer
            elif e.args[0] in [10053, 10054]:  # errno.ECONNABORTED:
                raise
            else:
                raise
        return ret

    def close(self):
        if hasattr(self.sock, "_closed") and not self.sock._closed:
            try:
                self.sock.shutdown(socket.SHUT_RDWR)
            except OSError as e:
                if e.errno != errno.ENOTCONN:  # 'Socket is not connected'
                    raise
            self.sock.close()
        else:
            self.sock.close()

    def recv_latest_frame(self):
        """
        非阻塞吸干缓冲区，只返回最新一帧 JPEG
        返回 None 表示当前无帧；连接断开会抛 ConnectionResetError
        """
        self.sock.setblocking(False)  # ① 切非阻塞
        latest_jpeg = None

        while True:
            # ② 读 4 B 头
            header = self._recv_nonblocking_exact(4)
            if header is None:
                break  # 内核缓冲区已空
            frame_size = struct.unpack("<I", header)[0]

            # ③ 读 JPEG 本体
            jpeg = self._recv_nonblocking_exact(frame_size)
            if jpeg is None:
                # 半包：把头塞回去，下次再读
                self.buf = header + self.buf
                break
            latest_jpeg = jpeg  # 只保留最新

        self.sock.setblocking(True)  # ④ 恢复原模式
        return latest_jpeg

    def _recv_nonblocking_exact(self, size):
        """非阻塞凑包，够 size 返回 bytes，否则 None"""
        while len(self.buf) < size:
            try:
                chunk = self.sock.recv(min(size - len(self.buf), 4096))
                if chunk == b"":  # 对端关闭
                    raise ConnectionResetError("peer closed")
                self.buf += chunk
            except BlockingIOError:  # 10035/11 统一捕获
                return None
        data, self.buf = self.buf[:size], self.buf[size:]
        return data
