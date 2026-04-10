# Minicap Experimental APK Build Guide

## Overview

This document describes the lazy mode implementation for the experimental Kotlin-based minicap and the complete build process.

## Lazy Mode Implementation

### What is Lazy Mode

Lazy mode (`-l` flag) is a pull-based screen capture mode where:
- Server captures frames only when client requests them
- Client sends 1 byte to request each frame
- Server uses `ImageReader.acquireLatestImage()` to get the latest frame (automatically discards intermediate frames)
- Much more efficient for use cases that don't need continuous streaming

### Protocol Flow (Lazy Mode)

```
1. Client connects → Server sends banner (24 bytes)
2. Client sends 1 byte (frame request)
3. Server: captureLatestFrame() → encode → send frame (4 bytes size + JPEG data)
4. Repeat from step 2
```

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
    
    # Read banner (24 bytes)
    banner = s.recv(24)
    version, size, pid, screen_w, screen_h, target_w, target_h, rotation, quirk = \
        struct.unpack('<BBIIIHHBB', banner)
    
    while True:
        # Send 1 byte request
        s.send(b'\x00')
        
        # Read frame size (4 bytes)
        size_data = s.recv(4)
        frame_size = struct.unpack('<I', size_data)[0]
        
        # Read frame data
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

- Original minicap binary: `/mnt/workspace/minicap/jni/minicap/`
- Android SurfaceControl API (private)
- Minicap protocol: 24-byte banner + frame data with 4-byte size prefix