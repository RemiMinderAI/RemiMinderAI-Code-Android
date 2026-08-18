# RemiMinder - AI Caregiving Companion

RemiMinder is an AI-powered caregiving app that helps MyCare users manage medications and allows caregivers to monitor loved ones through intelligent visit recording and smart reminders.

## Current Status

- **Phase 1**: React web app (archived in `phase1/` folder)
- **Phase 2**: Flutter mobile app with GCP backend (actively developing)

## Tech Stack

- **Mobile**: Flutter (iOS/Android) with Google ML Kit (OCR)
- **Backend**: FastAPI (Python)
- **Database**: Google Cloud SQL (PostgreSQL)
- **Authentication**: Firebase Auth
- **AI**: Google Gemini (visit transcription)
- **Deployment**: Google Cloud Run
- **Storage**: Google Cloud Storage

## Quick Start

### Local Development (Recommended)
```bash
# Start all services
docker-compose up

# Or run individually:
```

### Web App (Phase 1)
```bash
cd apps/web
npm install
npm start
```

### Mobile App (Phase 2)
```bash
cd apps/mobile
flutter pub get
flutter run
```

### Backend (Phase 2)
```bash
cd apps/backend
pip install -r requirements.txt
python main.py
```

## Project Structure

```
MediMinder/
├── apps/                    # Application code
│   ├── web/                # React web app (Phase 1)
│   ├── mobile/             # Flutter mobile app (Phase 2)
│   └── backend/            # FastAPI backend (Phase 2)
├── packages/               # Shared/reusable packages
│   └── shared/             # Cross-platform utilities
├── infrastructure/         # Infrastructure & deployment
│   └── deployment/         # GCP deployment configs
├── phase1/                 # Archived Phase 1 code
│   └── backend/            # Phase 1 FastAPI backend
├── docs/                   # Local development docs (gitignored)
└── docker-compose.yml      # Local development setup
```

## Key Features

### For MyCare Users
- **Audio Visit Recording**: Record care conversations with AI transcription
- **Smart Medication Reminders**: Intelligent scheduling with snooze and skip options
- **RemiScan**: AI-powered scanner for medication labels, pill bottles, and documents - extracts data, generates summaries, and creates smart reminders
- **Care Dashboard**: Track visits, medications, and adherence patterns
- **Visit Summaries**: AI-generated plain-language summaries of care visits

### For Caregivers
- **Multi-user monitoring**: Manage multiple MyCare users with real-time alerts
- **Adherence Tracking**: Monitor medication compliance and receive alerts
- **Visit Access**: View visit recordings and AI-generated summaries
- **Urgent contacts**: Quick access to the care team

### AI-Powered Features
- **Intelligent Reminders**: AI extracts and creates medication/appointment reminders
- **Natural Language Processing**: Friendly, jargon-free communication
- **Smart Scheduling**: Optimal timing for medication reminders
- **Voice Recording**: High-quality audio capture with background processing

## Documentation

- [Technical Architecture](./ARCHITECTURE.md)
- [Migration from Phase 1](./MIGRATION.md)
- [Phase 1 Archive](./phase1/README.md)
