# minicap_apk 性能优化 TDD 开发文档

## 1. 问题描述

### 1.1 现状
- minicap_apk 的 lazy 模式截图性能不佳
- 每帧延迟：rotation=0 时 ~1.5s，rotation=1 时 ~2s

### 1.2 初步分析
代码中存在多处 `time.sleep(0.5)`：

| 方法 | 行号 | 场景 |
|------|------|------|
| `get_frame()` | 181, 184, 189 | 每次单帧请求 |
| `_get_stream()` | 346,348,352,361,363,366 | 主循环每帧 |

---

## 2. TDD 开发记录

### Phase 1: 性能基准测试 ✅
- [x] 创建性能测试脚本
- [x] 获取当前性能基线

### Phase 2: 尝试移除 sleep ❌ (失败)

**修改内容**：
- 移除 `get_frame()` 中的 3 处 `time.sleep(0.5)`
- 移除 `_get_stream()` 中的 5 处 `time.sleep(0.5)`
- 合并 hybrid 请求为 `b"1\x00"` 一次发送

**测试结果**：
- ❌ **所有测试失败 - 超时**
- 服务器在收到请求后不响应帧数据

**回滚**：恢复原始代码

---

## 3. 根因分析

### 3.1 关键发现

**`time.sleep(0.5)` 是 minicap-debug.apk 正常工作的必要条件！**

移除 sleep 后，服务器不响应帧数据。这与 native minicap 的行为不同：
- **Native minicap**：发送请求后立即响应，无需额外等待
- **minicap-debug.apk**：需要等待时间让服务器准备帧数据

### 3.2 协议差异

| 特性 | Native minicap | minicap-debug.apk |
|------|----------------|-------------------|
| 响应速度 | 立即 | 需要等待 |
| sleep 需求 | 无 | **必需** |
| 预热时间 | 无 | ~0.5s |

---

## 4. 最终优化方案 ✅

### 4.1 智能等待机制

通过 `select()` 实现非阻塞等待：

```python
def _smart_recv(self, s, expected, max_wait=0.5, poll_interval=0.01):
    """
    Smart receive: try immediately first, then poll with select.
    """
    # Try immediate receive first (data might already be available)
    data = s.recv(expected)
    if len(data) == expected:
        return data

    # If not all data received, poll with select
    start_time = time.time()
    while time.time() - start_time < max_wait:
        ready, _, _ = select.select([s.sock], [], [], poll_interval)
        if ready:
            remaining = expected - len(data)
            chunk = s.recv(remaining)
            if not chunk:
                return None
            data += chunk
            if len(data) == expected:
                return data

    return data if data else None
```

### 4.2 主循环优化

**修改前**：
```python
while True:
    if lazy:
        s.sock.send(b"1")
        time.sleep(0.5)  # ← 固定等待 0.5s
        header = s.recv(4)
```

**修改后**：
```python
while True:
    if lazy:
        s.sock.send(b"1")
        header = self._smart_recv(s, expected=4, max_wait=1.0)  # ← 智能等待
        if header is None:
            continue  # 超时则重试
```

### 4.3 优化效果

| 指标 | 优化前 | 优化后 | 提升 |
|------|--------|--------|------|
| rotation=0 每帧 | ~1500ms | **~150ms** | **10x** |
| 主循环 sleep | 500ms | 智能等待 | - |

---

## 5. 测试结果

### 5.1 功能测试 ✅
- [x] `test_get_frames` 通过
- [x] `test_get_frame` 通过
- [x] 流式传输正常工作

### 5.2 性能测试结果

```
优化后的 minicap_apk 性能:

Frame 1: 4385ms (包含连接建立)
Frame 2: 128ms
Frame 3: 143ms
Frame 4: 151ms
Frame 5: 162ms
...

总耗时: 5729ms (10 帧)
FPS: 1.6
平均帧间隔: ~573ms
```

**关键发现**：
- 初始连接需要较长时间建立 (~4s)
- 连接建立后，每帧 ~150ms
- 智能等待机制正常工作

---

## 6. 代码修改摘要

### 6.1 新增方法
- `MinicapApk._smart_recv()`: 智能等待接收数据

### 6.2 修改方法
- `MinicapApk._get_stream()`: 主循环使用智能等待替代固定 sleep

### 6.3 保留的 sleep
- `get_frame()` 中的 sleep（单帧模式需要）
- 初始连接的 warmup sleep
- `get_frame_from_stream()` 中的 0.1s 最小间隔

---

## 7. 结论

### 7.1 优化成功
- 主循环帧延迟从 1.5s 降至 ~150ms（**10x 提升**）
- 功能测试全部通过

### 7.2 限制
- 初始连接仍需 ~1s 预热时间
- rotation=1 首帧约 4s（包含连接建立）
- minicap-debug.apk 的响应延迟是硬件/软件限制

### 7.3 进一步优化方向
1. 缓存连接，减少重连次数
2. 并行预热下一帧
3. APK 版本优化（需要修改 Java 端代码）
