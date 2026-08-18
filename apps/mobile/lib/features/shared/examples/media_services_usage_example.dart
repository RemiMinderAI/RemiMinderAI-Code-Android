// Example: How to use Camera and Audio services in your caregiving app screens

import 'package:flutter/material.dart';
import 'package:permission_handler/permission_handler.dart';
import '../../../core/services/camera_service.dart';
import '../../../core/services/audio_service.dart';
import '../../../core/services/permissions_service.dart';
import '../widgets/media_capture_widget.dart';

/// Example screen showing how to integrate media capture in caregiving workflows
class MediaServicesExampleScreen extends StatefulWidget {
  const MediaServicesExampleScreen({super.key});

  @override
  State<MediaServicesExampleScreen> createState() =>
      _MediaServicesExampleScreenState();
}

class _MediaServicesExampleScreenState
    extends State<MediaServicesExampleScreen> {
  final CameraService _cameraService = CameraService();
  final AudioService _audioService = AudioService();

  final List<String> _capturedImages = [];
  final List<String> _recordedAudios = [];

  @override
  void initState() {
    super.initState();
    _initializeServices();
  }

  Future<void> _initializeServices() async {
    // Initialize camera service
    await _cameraService.initialize();

    // Request permissions on first use
    await PermissionsService().requestMediaPermissions(context);
  }

  @override
  void dispose() {
    _audioService.dispose();
    _cameraService.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(
        title: const Text('Media Services Example'),
        backgroundColor: Theme.of(context).colorScheme.primary,
        foregroundColor: Colors.white,
      ),
      body: SingleChildScrollView(
        padding: const EdgeInsets.all(16),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            // Built-in Media Capture Widget
            MediaCaptureWidget(
              onPhotoCaptured: _handlePhotoCaptured,
              onAudioRecorded: _handleAudioRecorded,
            ),

            const SizedBox(height: 32),

            // Custom Implementation Examples
            _buildCustomExamples(),

            const SizedBox(height: 32),

            // Captured Media Display
            if (_capturedImages.isNotEmpty || _recordedAudios.isNotEmpty)
              _buildCapturedMediaList(),
          ],
        ),
      ),
    );
  }

  Widget _buildCustomExamples() {
    return Card(
      child: Padding(
        padding: const EdgeInsets.all(16),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text(
              'Custom Implementation Examples',
              style: Theme.of(context).textTheme.titleLarge?.copyWith(
                    fontWeight: FontWeight.bold,
                  ),
            ),
            const SizedBox(height: 16),

            // Direct Camera Usage
            ListTile(
              leading: const Icon(Icons.camera_alt, color: Colors.blue),
              title: const Text('Scan Label'),
              subtitle: const Text('Take photo of medication label'),
              trailing: ElevatedButton(
                onPressed: _scanPrescription,
                child: const Text('Scan'),
              ),
            ),

            const Divider(),

            // Direct Audio Usage
            ListTile(
              leading: Icon(
                Icons.mic,
                color: _audioService.isRecording ? Colors.red : Colors.green,
              ),
              title: Text(_audioService.isRecording
                  ? 'Recording...'
                  : 'Record Care Notes'),
              subtitle: Text(_audioService.isRecording
                  ? 'Duration: ${_audioService.getFormattedDuration()}'
                  : 'Record important care instructions'),
              trailing: ElevatedButton(
                onPressed: _audioService.isRecording
                    ? _stopAudioRecording
                    : _startAudioRecording,
                style: ElevatedButton.styleFrom(
                  backgroundColor:
                      _audioService.isRecording ? Colors.red : Colors.green,
                ),
                child: Text(_audioService.isRecording ? 'Stop' : 'Record'),
              ),
            ),

            const Divider(),

            // Permission Management
            ListTile(
              leading: const Icon(Icons.settings, color: Colors.orange),
              title: const Text('Manage Permissions'),
              subtitle:
                  const Text('Check and request camera/microphone access'),
              trailing: ElevatedButton(
                onPressed: _managePermissions,
                child: const Text('Check'),
              ),
            ),
          ],
        ),
      ),
    );
  }

  Widget _buildCapturedMediaList() {
    return Card(
      child: Padding(
        padding: const EdgeInsets.all(16),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text(
              'Captured Media',
              style: Theme.of(context).textTheme.titleLarge?.copyWith(
                    fontWeight: FontWeight.bold,
                  ),
            ),
            const SizedBox(height: 16),

            // Images
            if (_capturedImages.isNotEmpty) ...[
              const Text('Images:',
                  style: TextStyle(fontWeight: FontWeight.bold)),
              ..._capturedImages.map((path) => ListTile(
                    leading: const Icon(Icons.image),
                    title: Text('Image: ${path.split('/').last}'),
                    trailing: IconButton(
                      icon: const Icon(Icons.delete),
                      onPressed: () => _deleteImage(path),
                    ),
                  )),
              const SizedBox(height: 16),
            ],

            // Audio Recordings
            if (_recordedAudios.isNotEmpty) ...[
              const Text('Audio Recordings:',
                  style: TextStyle(fontWeight: FontWeight.bold)),
              ..._recordedAudios.map((path) => ListTile(
                    leading: const Icon(Icons.audio_file),
                    title: Text('Recording: ${path.split('/').last}'),
                    trailing: Row(
                      mainAxisSize: MainAxisSize.min,
                      children: [
                        IconButton(
                          icon: const Icon(Icons.play_arrow),
                          onPressed: () => _playAudio(path),
                        ),
                        IconButton(
                          icon: const Icon(Icons.delete),
                          onPressed: () => _deleteAudio(path),
                        ),
                      ],
                    ),
                  )),
            ],
          ],
        ),
      ),
    );
  }

  // Event Handlers
  void _handlePhotoCaptured(String? imagePath) {
    if (imagePath != null) {
      setState(() => _capturedImages.add(imagePath));
      ScaffoldMessenger.of(context).showSnackBar(
        const SnackBar(content: Text('Photo captured successfully!')),
      );
    }
  }

  void _handleAudioRecorded(String? audioPath) {
    if (audioPath != null) {
      setState(() => _recordedAudios.add(audioPath));
      ScaffoldMessenger.of(context).showSnackBar(
        const SnackBar(content: Text('Audio recorded successfully!')),
      );
    }
  }

  Future<void> _scanPrescription() async {
    try {
      final imagePath = await _cameraService.scanDocument(context);
      if (imagePath != null) {
        _handlePhotoCaptured(imagePath);
        // Here you could add OCR processing for prescription data
        _showPrescriptionProcessingDialog(imagePath);

        // Clean up temporary image file after processing
        await _cameraService.cleanupImageFile(imagePath);
      }
    } catch (e) {
      _showErrorDialog('Failed to scan label', e.toString());
    }
  }

  Future<void> _startAudioRecording() async {
    final success = await _audioService.startRecording(context);
    if (success) {
      setState(() {});
      ScaffoldMessenger.of(context).showSnackBar(
        const SnackBar(content: Text('Recording started...')),
      );
    }
  }

  Future<void> _stopAudioRecording() async {
    final audioPath = await _audioService.stopRecording();
    if (audioPath != null) {
      _handleAudioRecorded(audioPath);
    }
    setState(() {});
  }

  Future<void> _managePermissions() async {
    final statuses =
        await PermissionsService().requestMediaPermissions(context);

    final cameraGranted = statuses[Permission.camera]?.isGranted ?? false;
    final micGranted = statuses[Permission.microphone]?.isGranted ?? false;

    if (!mounted) return;

    showDialog(
      context: context,
      builder: (context) => AlertDialog(
        title: const Text('Permission Status'),
        content: Column(
          mainAxisSize: MainAxisSize.min,
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text('Camera: ${cameraGranted ? 'Granted' : 'Denied'}'),
            Text('Microphone: ${micGranted ? 'Granted' : 'Denied'}'),
          ],
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.of(context).pop(),
            child: const Text('OK'),
          ),
        ],
      ),
    );
  }

  Future<void> _showPrescriptionProcessingDialog(String imagePath) async {
    showDialog(
      context: context,
      barrierDismissible: false,
      builder: (context) => const AlertDialog(
        title: Text('Processing Label'),
        content: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            CircularProgressIndicator(),
            SizedBox(height: 16),
            Text('Analyzing label data...'),
          ],
        ),
      ),
    );

    // Simulate processing delay
    await Future.delayed(const Duration(seconds: 2));

    if (!mounted) return;
    Navigator.of(context).pop();

    // Show mock results
    showDialog(
      context: context,
      builder: (context) => AlertDialog(
        title: const Text('Label Detected'),
        content: const Column(
          mainAxisSize: MainAxisSize.min,
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text('Medication: Lisinopril'),
            Text('Amount: 10mg'),
            Text('Frequency: Once daily'),
            Text('Instructions: Take with food'),
          ],
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.of(context).pop(),
            child: const Text('Save'),
          ),
          TextButton(
            onPressed: () => Navigator.of(context).pop(),
            child: const Text('Edit'),
          ),
        ],
      ),
    );
  }

  void _playAudio(String path) {
    // TODO: Implement audio playback using audio service
    ScaffoldMessenger.of(context).showSnackBar(
      SnackBar(content: Text('Playing: ${path.split('/').last}')),
    );
  }

  void _deleteImage(String path) {
    setState(() => _capturedImages.remove(path));
    ScaffoldMessenger.of(context).showSnackBar(
      const SnackBar(content: Text('Image deleted')),
    );
  }

  void _deleteAudio(String path) {
    setState(() => _recordedAudios.remove(path));
    ScaffoldMessenger.of(context).showSnackBar(
      const SnackBar(content: Text('Audio deleted')),
    );
  }

  void _showErrorDialog(String title, String message) {
    showDialog(
      context: context,
      builder: (context) => AlertDialog(
        title: Text(title),
        content: Text(message),
        actions: [
          TextButton(
            onPressed: () => Navigator.of(context).pop(),
            child: const Text('OK'),
          ),
        ],
      ),
    );
  }
}

// Enum for recording states (if you need more complex state management)
enum RecordingState {
  ready,
  recording,
  paused,
  completed,
}
