# MediMinder Mobile App

A Flutter-based caregiving companion app for MyCare users and caregivers.

## Getting Started

### Prerequisites

- Flutter SDK (>=3.0.0)
- Dart SDK (>=3.0.0)
- Android Studio (for Android development)
- Xcode (for iOS development, macOS only)

### Installation

1. **Clone the repository**
   ```bash
   git clone https://github.com/your-username/MediMinder.git
   cd MediMinder/mobile
   ```

2. **Install dependencies**
   ```bash
   flutter pub get
   ```

3. **Run the app**
   ```bash
   flutter run
   ```

### Project Structure

```
lib/
├── features/
│   ├── patient/          # MyCare screens and logic
│   ├── caregiver/        # Caregiver-specific screens and logic
│   └── auth/             # Authentication screens and logic
├── core/
│   ├── models/          # Data models
│   ├── services/        # API services, database services
│   └── widgets/         # Reusable UI components
└── main.dart            # App entry point
```

## Features

### For MyCare Users
- **Visit Recording**: Record care visits with audio
- **Medication Reminders**: Smart reminders with notifications
- **Care Dashboard**: Track visits, medications, and care metrics
- **Caregiver Connection**: Share care information with caregivers

### For Caregivers
- **MyCare user management**: Monitor multiple people you support
- **Visit Summaries**: Access visit recordings and summaries
- **Reminder Oversight**: Track medication adherence
- **Communication**: Connect with MyCare users and the care team

## Development

### Code Style
This project follows the [Flutter Style Guide](https://flutter.dev/docs/development/tools/formatting).

### Testing
```bash
flutter test
```

### Building for Release
```bash
# Android APK
flutter build apk --release

# iOS (macOS only)
flutter build ios --release
```

## Backend Integration

The mobile app communicates with the FastAPI backend located in `../backend/`. Make sure the backend is running before testing API integrations.

## Contributing

1. Create a feature branch from `main`
2. Make your changes
3. Add tests for new functionality
4. Submit a pull request

## Related Documentation

- [Root Project README](../README.md)
- [Backend API Documentation](../backend/)
- [Phase 1 Web App Archive](../phase1/README.md)