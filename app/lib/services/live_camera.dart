// The front camera, running live inside the app for the live selfie: not the phone's camera app and
// not the gallery, so an old photo can't be picked. Tests swap in their own camera.
import 'dart:typed_data';

import 'package:camera/camera.dart';
import 'package:flutter/widgets.dart';

class CameraUnavailable implements Exception {
  CameraUnavailable(this.why);
  final String why; // no_camera, denied, failed
  @override
  String toString() => 'CameraUnavailable($why)';
}

abstract class LiveCamera {
  /// Opens the front camera. Throws [CameraUnavailable].
  Future<void> start();

  /// What the camera sees, filling the space it's given.
  Widget preview();

  /// One still frame (JPEG).
  Future<Uint8List> capture();

  Future<void> dispose();
}

typedef LiveCameraFactory = LiveCamera Function();

/// Tests replace this with their own.
LiveCameraFactory openLiveCamera = _PluginCamera.new;

class _PluginCamera implements LiveCamera {
  CameraController? _c;

  @override
  Future<void> start() async {
    final List<CameraDescription> cams;
    try {
      cams = await availableCameras();
    } on CameraException {
      throw CameraUnavailable('failed');
    }
    if (cams.isEmpty) throw CameraUnavailable('no_camera');
    final front = cams.where((c) => c.lensDirection == CameraLensDirection.front).firstOrNull ?? cams.first;
    // Medium resolution: plenty for the face models, and quick to send. No microphone.
    final c = CameraController(front, ResolutionPreset.medium, enableAudio: false, imageFormatGroup: ImageFormatGroup.jpeg);
    try {
      await c.initialize();
    } on CameraException catch (e) {
      await c.dispose();
      throw CameraUnavailable(e.code.toLowerCase().contains('denied') ? 'denied' : 'failed');
    }
    _c = c;
  }

  @override
  Widget preview() {
    final c = _c!;
    final size = c.value.previewSize;
    if (size == null) return CameraPreview(c);
    // The preview size is reported in landscape; show it upright, covering the oval.
    return FittedBox(
      fit: BoxFit.cover,
      child: SizedBox(width: size.height, height: size.width, child: CameraPreview(c)),
    );
  }

  @override
  Future<Uint8List> capture() async => (await _c!.takePicture()).readAsBytes();

  @override
  Future<void> dispose() async {
    final c = _c;
    _c = null;
    await c?.dispose();
  }
}
