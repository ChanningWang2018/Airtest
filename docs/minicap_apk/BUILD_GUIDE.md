# Minicap Experimental APK Build Guide

## Overview

This document describes the lazy mode implementation for the experimental Kotlin-based minicap and the complete build process.

## APK Source

- Upstream: https://github.com/ChanningWang2018/minicap (fork of DeviceFarmer/minicap)
- Prebuilt APK: `experimental/app/prebuild/minicap-debug.apk` in the fork repository
- Reference Python client: `experimental/server/minicap_apk.py` in the fork repository (local copy in this repo: `reference/minicap_apk_fork_client.py`)

## Lazy Mode Implementation

### What is Lazy Mode

Lazy mode (`-l` flag) is a pull-based screen capture mode where:
- Server captures frames only when client requests them
- Client sends a single byte `b"1"` to request each frame
- Server uses `ImageReader.acquireLatestImage()` to get the latest frame (automatically discards intermediate frames)
- Much more efficient for use cases that don't need continuous streaming

### Protocol (Lazy Mode)

- On connect, the server sends a 24-byte banner in the standard minicap format (`<2B5I2B>`).
- Lazy mode is strictly request/response: the client sends a single byte `b"1"` before each frame, and the server replies with one frame (4-byte little-endian length + JPEG data).
- After a request, the server waits up to 2s for the first frame; if none arrives, the request is dropped and nothing is sent. The client receive timeout should therefore be 3s.
- No interval throttling is needed between requests: `-r` only controls how often the server-side cached bitmap is refreshed.
- There is NO hybrid request protocol (no combined `b"1"` + `b"\x00"` request, no extra prime bytes). Send `b"1"` only, one byte per frame.
- Entry point: `io.devicefarmer.minicap.Main`, launched via `app_process` (see Usage below).

### Code Changes

#### 1. Main.kt
- Added `-l` flag parsing
- Added lazy mode help text
- Wired `lazyMode` to provider

```kotlin
// In argument parsing:
"-l" -> p.lazyMode(true)

// Wired to provider:
provider.lazyMode = params.lazyMode
```

#### 2. Parameters.kt
- Added `lazyMode: Boolean` to Parameters class
- Added `lazyMode(e: Boolean)` builder method

#### 3. SimpleServer.kt
- Added `onClientRequest(socket: LocalSocket)` to Listener interface
- Added loop in `start()` to continuously handle client requests

```kotlin
interface Listener {
    fun onConnection(socket: LocalSocket)
    fun onClientRequest(socket: LocalSocket)
}

fun start() {
    try {
        val serverSocket = LocalServerSocket(socket)
        log.info("Listening on socket : ${socket}")
        val clientSocket: LocalSocket = serverSocket.accept()
        listener.onConnection(clientSocket)
        while (true) {
            listener.onClientRequest(clientSocket)
        }
    } catch (e: IOException) {
        log.error("error waiting connection", e)
    }
}
```

#### 4. BaseProvider.kt
- Added `lazyMode: Boolean` property
- Modified `onImageAvailable()` to discard frames in lazy mode
- Added `onClientRequest()` to handle client requests
- Added `captureLatestFrame()` method

```kotlin
var lazyMode: Boolean = false

override fun onImageAvailable(reader: ImageReader) {
    val image = reader.acquireLatestImage()
    if (lazyMode) {
        image?.close()  // Discard frames in lazy mode
        return
    }
    // Normal mode: process with frame rate limiting
    ...
}

override fun onClientRequest(socket: LocalSocket) {
    if (lazyMode) {
        socket.inputStream.read()  // Wait for client request byte
        captureLatestFrame()
    }
}

fun captureLatestFrame() {
    val image = imageReader.acquireLatestImage()
    if (image != null) {
        encode(image, quality, clientOutput.imageBuffer)
        clientOutput.send()
        image.close()
    }
}
```

## Build Process

### Environment Requirements

- Java 17 JDK
- Android SDK with platform 34 and build-tools
- Gradle 8.10.2 (included in project)

### Build Commands

```bash
# Set environment variables
export JAVA_HOME=/usr/lib/jvm/java-17-openjdk-amd64
export ANDROID_HOME=/usr/lib/android-sdk

# Build APK
cd /mnt/workspace/minicap/experimental
./gradlew assembleDebug
```

### APK Location

```
/mnt/workspace/minicap/experimental/app/build/outputs/apk/debug/minicap-debug.apk
```

### Build Configuration Changes

The following changes were made to `build.gradle` files to fix compilation issues:

#### Root build.gradle
- AGP: 8.1.0
- Kotlin: 1.8.22

#### App build.gradle
- compileSdkVersion: 34
- targetSdkVersion: 34
- Java: 17

#### themes.xml
- Simplified to use basic Android theme to avoid Material dependency issues

## Usage

### Installation

```bash
# Push APK to device
adb push minicap-debug.apk /data/local/tmp/minicap.apk

# Make executable (if needed)
adb shell chmod 755 /data/local/tmp/minicap.apk
```

### Running with Lazy Mode

```bash
adb shell CLASSPATH=/data/local/tmp/minicap.apk app_process /system/bin \
  io.devicefarmer.minicap.Main \
  -l \
  -P 1920x1080@1920x1080/0 \
  -n minicap
```

### Command Line Options

```
-d <id>:       Display ID. (0)
-n <name>:     Change the name of the abstract unix domain socket. (minicap)
-P <value>:    Display projection (<w>x<h>@<w>x<h>/{0|90|180|270}).
               The requested target size is honored EXACTLY in encoded frames
               (lazy, push and -s modes): the capture buffer keeps the display
               aspect ratio and the bitmap is rescaled to the exact requested
               size right before JPEG encoding (no rescale if the aspects
               already match). Pass --fit-projection to restore the legacy
               behavior of fitting the target to the display aspect ratio
               (e.g. @360x640 on a 16:9 display yields 360x203 frames).
--fit-projection: Legacy fit-to-aspect projection mode (see -P above).
-Q <value>:    JPEG quality (0-100).
-s:            Take a screenshot and output it to stdout. Needs -P.
-S:            Skip frames when they cannot be consumed quickly enough.
-r <value>:    Frame rate (frames/s)
-t:            Attempt to get the capture method running, then exit.
-i:            Get display information in JSON format. May segfault.
-l:            Lazy mode - capture frame on client request.
-h:            Show help.
```

### Client Implementation (Python Example)

```python
import socket
import struct

def connect_minicap_lazy(socket_path):
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.connect(socket_path)
    s.settimeout(3)  # server may take up to 2s for the first frame

    # Read banner (24 bytes, standard minicap format)
    banner = s.recv(24)
    version, size, pid, screen_w, screen_h, target_w, target_h, rotation, quirk = \
        struct.unpack('<2B5I2B', banner)

    while True:
        # Send single-byte frame request
        s.send(b'1')

        # Read frame size (4 bytes, little-endian)
        size_data = s.recv(4)
        if len(size_data) < 4:
            continue  # request dropped, no frame arrived within 2s
        frame_size = struct.unpack('<I', size_data)[0]

        # Read frame data (JPEG)
        frame = s.recv(frame_size)

        # Process frame (e.g., decode JPEG)
        yield frame

# Usage
for frame in connect_minicap_lazy('/tmp/minicap'):
    # Do something with frame
    pass
```

## Files Modified

### For Lazy Mode Implementation

| File | Description |
|------|-------------|
| `app/src/main/java/io/devicefarmer/minicap/Main.kt` | Added `-l` flag parsing |
| `app/src/main/java/io/devicefarmer/minicap/Parameters.kt` | Added lazyMode parameter |
| `app/src/main/java/io/devicefarmer/minicap/SimpleServer.kt` | Added request loop |
| `app/src/main/java/io/devicefarmer/minicap/provider/BaseProvider.kt` | Added lazy mode logic |

### For Build Fixes

| File | Description |
|------|-------------|
| `build.gradle` | Updated AGP and Kotlin versions |
| `app/build.gradle` | Updated SDK versions |
| `app/src/main/res/values/themes.xml` | Simplified theme |

## Troubleshooting

### Build Errors

1. **Java not found**: Set `JAVA_HOME` to Java 17 path
2. **Android SDK not found**: Set `ANDROID_HOME` or create `local.properties` with `sdk.dir`
3. **License not accepted**: Accept licenses or create dummy license files:
   ```bash
   mkdir -p $ANDROID_HOME/licenses
   echo -e "\n24333f8a63b6825ea9c5514f83c2829b004d1fee" > $ANDROID_HOME/licenses/android-sdk-license
   echo -e "\n84831b9409646a918e30573bab4c9c91346d8abd" > $ANDROID_HOME/licenses/android-sdk-preview-license
   ```

### Runtime Issues

1. **Segfault with `-i` flag**: Known issue, use with caution
2. **Black screen**: Try using DisplayManager API fallback (Android 15+)
3. **Permission denied**: Ensure proper Android permissions

## References

- APK source: https://github.com/ChanningWang2018/minicap (fork of DeviceFarmer/minicap)
- Reference client: `experimental/server/minicap_apk.py` in the fork repository (local copy: `reference/minicap_apk_fork_client.py`)
- Original minicap binary: `/mnt/workspace/minicap/jni/minicap/`
- Android SurfaceControl API (private)
- Minicap protocol: 24-byte banner + frame data with 4-byte size prefix