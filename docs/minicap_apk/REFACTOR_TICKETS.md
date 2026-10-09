# MinicapApk 重构 Tickets

来源:2026-10-09 code review(范围 `143bbb6...HEAD`)+ 新 APK 升级
(https://github.com/ChanningWang2018/minicap,`experimental/app/prebuild/minicap-debug.apk`)。
配套参考实现:仓库内 `reference/minicap_apk_fork_client.py`(fork 作者维护、与新 APK 配对验证过的客户端)。

## 执行状态(2026-10-09)

- T1 ✅ 已完成(含后续修正:projection 重建比较改为归一化比对,None 也参与判差异)
- T2 ✅ 已完成
- T3 ✅ 已完成
- T4 ✅ 已完成:公共基类 `cap_methods/minicap_base.py`,minicap.py 逻辑零改动
- T5 ✅ 已完成:SM_S9210(127.0.0.1:16384)全项通过——md5 自动升级、quirk resetup、
  连续帧均值 ~0.05s、projection 重建、录制器路径、旋转重建、teardown 后无残留进程/forward

**实测发现(APK 上游行为)**:~~新 APK 对 `-P` 的 projection 做"保持宽高比的适配缩放"~~
→ **已解决**:fork 提交 d0fde0f(honor the exact -P projection size)+ b2bfc72(重打包 APK,
md5 `18e9bdb2…`)后默认精确输出 `-P` 尺寸(编码前重采样),旧行为改由 `--fit-projection`
显式开启。2026-10-09 复测通过:四组 projection 的 JPEG 实际尺寸与服务器 `INFO:` target
逐项一致,投影帧内容正常(std≈65),客户端 md5 自动升级路径同时得到复验。

## 新 APK 协议要点(LAZY_MODE.md)

- 请求帧:客户端每帧前发送单字节 `b"1"`;服务器 `requestFrame()` 阻塞读 1 字节
- banner:连接后 24 字节,`<2B5I2B>` 标准 minicap 格式
- 帧:`4 字节小端长度 + JPEG`,与 push 模式一致
- 首帧:服务器最多等 2s;等不到则丢弃该请求(什么都不发)→ 客户端收包超时需设 3s
- 请求间无需间隔限流(`-r` 只影响服务器缓存位图刷新频率)
- 无 hybrid 请求;入口类名不变:`io.devicefarmer.minicap.Main`
- 服务器就绪输出行:`Listening on socket : minicap_apk_...`

## T1【P0】minicap_apk.py 回归参考实现,修复行为缺陷

文件:`airtest/core/android/cap_methods/minicap_apk.py`(仅此文件)

以 `reference/minicap_apk_fork_client.py` 的 `_get_stream` 循环为基准,保留 HEAD 的 60s 服务器启动等待。具体:

1. **删除 hybrid 请求**:删 `_use_hybrid_request`、`b"1"+b"\x00"` 连发、双 `sleep(0.5)` 初始请求块——新 APK 不存在该协议。
2. **删除 `_smart_recv`**:其 select 轮询分支因 `SafeSocket.recv` 内部凑满而不可达。改用 `s.recv_with_timeout(4, self.RECVTIMEOUT)`,`RECVTIMEOUT = 3`(协议推荐:服务器首帧等 2s、客户端超时 3s)。
3. **修复双重 prime 丢帧**:`get_stream` 内部已消费首个 `yield stopping`;`get_frame_via_stream` 不得再额外 `next()` 预热(现状每次建流丢一帧 + 固定 0.5s)。
4. **有界失败处理**:header 超时 → 参考实现行为 `stopping = yield None`,由调用方决定;`get_frame_via_stream` 收到 None 做有界重试(≤2 次)后抛 `ScreenError`;旋转事件触发时清事件并 `return` 结束生成器。
5. **修复 teardown**:`stopping = yield frame_data` 消费 send 进来的值,使 `teardown_stream` 的 `frame_gen.send(1)` 机制恢复有效(配合 `while not stopping`)。
6. **修复 banner 泄漏**:socket/banner 接收置于 try 内,失败时执行 cleanup(proc/nbsp/socket/forward),再抛异常。
7. **projection 支持**:`get_frame(projection)` 不再静默丢弃参数——与流当前 projection 不同时,以该 projection 重建流(`_get_params(projection)` 传参贯穿);相同时复用流。
8. **删除请求间隔限流**:删 `_last_request_time`/0.1s 等待(协议明确无需间隔)。统一 `get_frame_from_stream` 与 `get_frame_via_stream` 的取帧路径/状态处理(录制器走 `get_frame_from_stream`)。
9. **升级检测**:`install_or_upgrade` 仅查存在导致旧 APK 永不更新。改为比对本地与设备 APK md5(`adb shell md5sum`,失败回退到存在即跳过)。
10. 清理死属性:`RECVTIMEOUT` 恢复使用;`VERSION`、`stream_lock`、`_stream_rotation` 若仍无用则删除;类 docstring 的 reference URL 更新为 fork 地址。

验收:py_compile / import 通过;公开 API 不变(`get_frame`、`get_frame_from_stream`、`get_frame_via_stream`、`get_stream`、`snapshot`、`update_rotation`、`teardown_stream`);真机 smoke test 通过(T5)。

## T2【P1】safesocket.py 死代码清理

文件:`airtest/utils/safesocket.py`(仅此文件)

- 删除 `recv_latest_frame` / `_recv_nonblocking_exact`:全仓库无调用方,且异常路径会把 socket 留在非阻塞态、`setblocking(True)` 覆盖已有 timeout。
- 确认 `recv_with_timeout` 行为正确(T1 依赖它)。

## T3【P1】screen_proxy 优先级文档 + 三份文档对齐现状

文件:`airtest/core/android/cap_methods/screen_proxy.py`(仅 docstring)、`docs/minicap_apk/*`

- `auto_setup` docstring 写明实际回退顺序:MINICAP > MINICAP_APK > JAVACAP > ADBCAP,及 MINICAP_APK 的定位(无 root/无原生 minicap.so 设备的截图手段)。
- `BUILD_GUIDE.md`:APK 来源改为 ChanningWang2018/minicap(prebuild 路径),协议节按上文"协议要点"重写(删 hybrid 描述)。
- `LAZY_MODE_IMPROVEMENT.md`:文首加状态注记——方案已由 fork 新 APK + 参考客户端实现取代,文中 diagnose()/get_frame_on_demand() 等未落地项标注废弃。
- `minicap_apk_performance_tdd.md`:标注被新 APK 推翻的结论(§6.3 "必须保留 sleep" 不再成立;hybrid 为已证伪实验)。

## T4【P2,依赖 T1】与 minicap.py 抽公共基类

文件:`minicap.py`、`minicap_apk.py`、新增共享模块

两文件大面积逐字重复(`__init__`/`_get_params`/`get_stream`/`snapshot`/`update_rotation`/`_cleanup*`/`teardown_stream`/`_setup_stream_server` 骨架/`retry_when_socket_error`)。抽 `MinicapBase`(或 mixin),两者保留各自协议差异,公开 API 与行为不变。必须在 T1 落地后进行。

## T5【P0】真机回归

设备 `127.0.0.1:16364`(SM_S9210)。验证:MINICAP_APK 方式 snapshot 成功且非黑屏、连续 snapshot 计时、横竖屏旋转后恢复、teardown 无残留进程/forward、与 MINICAP/JAVACAP 回退链正常。
