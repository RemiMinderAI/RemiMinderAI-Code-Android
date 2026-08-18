import 'dart:io';
import 'package:camera/camera.dart';
import 'package:flutter/material.dart';
import 'package:image_picker/image_picker.dart';
import 'package:permission_handler/permission_handler.dart';
import 'permissions_service.dart';

/// Service for handling camera operations (scanning pill bottles, lab reports, etc.)
class CameraService {
  static final CameraService _instance = CameraService._internal();
  factory CameraService() => _instance;
  CameraService._internal();

  final ImagePicker _imagePicker = ImagePicker();
  CameraController? _cameraController;

  /// Initialize camera service
  Future<void> initialize() async {
    // Camera service is ready to use with ImagePicker
    // No additional initialization needed for basic photo capture
  }

  /// Take a photo with camera for scanning
  Future<XFile?> takePhoto(BuildContext context) async {
    try {
      // Request camera permission
      final permissionStatus =
          await PermissionsService().requestCameraPermission(context);
      if (permissionStatus != PermissionStatus.granted) {
        return null;
      }

      // Open camera - prevent automatic gallery save for medical scanning
      final XFile? photo = await _imagePicker.pickImage(
        source: ImageSource.camera,
        preferredCameraDevice: CameraDevice.rear,
        imageQuality: 85, // Good quality for OCR
        requestFullMetadata: false, // Prevent unnecessary metadata
      );

      return photo;
    } catch (e) {
      debugPrint('Error taking photo: $e');
      return null;
    }
  }

  /// Pick image from gallery
  Future<XFile?> pickFromGallery(BuildContext context) async {
    try {
      final XFile? image = await _imagePicker.pickImage(
        source: ImageSource.gallery,
        imageQuality: 85,
      );
      return image;
    } catch (e) {
      debugPrint('Error picking from gallery: $e');
      return null;
    }
  }

  /// Scan pill bottle or lab report
  Future<String?> scanDocument(BuildContext context) async {
    final photo = await takePhoto(context);
    if (photo != null) {
      // TODO: Implement OCR processing here
      // For now, return file path
      return photo.path;
    }
    return null;
  }

  /// Clean up temporary image file after processing
  Future<bool> cleanupImageFile(String filePath) async {
    try {
      final file = File(filePath);
      if (await file.exists()) {
        await file.delete();
        return true;
      }
      return false;
    } catch (e) {
      debugPrint('Error cleaning up image file: $e');
      return false;
    }
  }

  /// Show camera/gallery picker dialog
  Future<XFile?> showImagePickerDialog(BuildContext context) async {
    return showDialog<XFile>(
      context: context,
      builder: (BuildContext context) {
        return AlertDialog(
          title: const Text('Select Image Source'),
          content: const Text('Choose how you want to add the document photo.'),
          actions: [
            TextButton(
              onPressed: () => Navigator.of(context).pop(),
              child: const Text('Cancel'),
            ),
            TextButton(
              onPressed: () async {
                Navigator.of(context).pop();
                final result = await pickFromGallery(context);
                if (result != null && context.mounted) {
                  Navigator.of(context).pop(result);
                }
              },
              child: const Text('Gallery'),
            ),
            ElevatedButton(
              onPressed: () async {
                Navigator.of(context).pop();
                final result = await takePhoto(context);
                if (result != null && context.mounted) {
                  Navigator.of(context).pop(result);
                }
              },
              child: const Text('Camera'),
            ),
          ],
        );
      },
    );
  }

  /// Process scanned image for OCR (placeholder for future implementation)
  Future<String?> processScannedImage(String imagePath) async {
    // TODO: Implement OCR processing
    // This could integrate with Google ML Kit, Tesseract, or other OCR services
    // For healthcare documents, consider HIPAA compliance

    // Placeholder return
    return 'Processed text from image: $imagePath';
  }

  /// Dispose camera resources
  void dispose() {
    _cameraController?.dispose();
    _cameraController = null;
  }
}
