import 'package:firebase_auth/firebase_auth.dart';

import '../core/services/backend_api_service.dart';
import '../models/connected_patient.dart';

/// The caregiver's roster, from care_team_members via
/// `GET /api/care-team/my-patients`.
///
/// This used to read users/{uid}/connectedPatients in Firestore — documents
/// only this app ever wrote, which the backend neither reads nor maintains.
class MyPatientsRepository {
  MyPatientsRepository({
    FirebaseAuth? auth,
    BackendApiService? api,
  })  : _auth = auth ?? FirebaseAuth.instance,
        _api = api ?? BackendApiService();

  final FirebaseAuth _auth;
  final BackendApiService _api;

  Future<List<ConnectedPatient>> fetchMyPatients() async {
    if (_auth.currentUser == null) {
      // Not an empty roster. Returning [] here would render as "no patients
      // yet", which is how this class of bug has stayed hidden.
      throw StateError('Not signed in');
    }
    final rows = await _api.getMyPatients();
    return rows.map(ConnectedPatient.fromRoster).toList();
  }
}
