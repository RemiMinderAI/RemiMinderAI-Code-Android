# Phase 1 → Phase 2 Migration Guide

## What Changed

| Component | Phase 1 | Phase 2 |
|-----------|---------|---------|
| Frontend | React (Vercel) | Flutter (App Stores) |
| Backend | FastAPI (current server) | FastAPI (GCP Cloud Run) |
| Database | Supabase | Cloud SQL + PowerSync |
| Auth | Supabase Auth | Firebase Auth (JWT-based) |

## Reusable Code

Copy these files from Phase 1:

- `phase1/server/main_backend/services/ai_service.py` → `backend/services/`
- `phase1/server/main_backend/services/db_reminders.py` → `backend/services/`
- `phase1/server/main_backend/schemas/reminder_schemas.py` → `backend/models/`

## Next Steps

1. **Set up Flutter development environment**
   ```bash
   cd mobile
   flutter doctor
   flutter create . --project-name mediminder
   ```

2. **Configure GCP Cloud Run deployment**
   ```bash
   cd deployment/phase2-gcp
   # Create deployment scripts
   ```

3. **Adapt backend APIs for mobile**
   - Modify authentication flows
   - Add offline-first capabilities
   - Implement push notifications

4. **Build Flutter screens**
   - Member dashboard
   - Caregiver interface
   - Settings and profile management

5. **Test end-to-end functionality**
   - Audio recording and transcription
   - Reminder notifications
   - Caregiver-Member communication

## Migration Checklist

- [ ] Phase 1 code archived in `phase1/` directory
- [ ] Flutter project initialized in `mobile/`
- [ ] Backend code copied to `backend/`
- [ ] Shared constants defined
- [ ] GCP deployment configured
- [ ] Database schema migrated
- [ ] Authentication adapted for mobile
- [ ] Push notifications implemented
- [ ] Offline capabilities added
- [ ] Mobile app published to stores