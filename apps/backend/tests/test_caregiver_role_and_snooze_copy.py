"""Where the caregiver role comes from, and what a refused snooze says."""

import asyncio, contextlib, inspect, os, sys, unittest
from unittest import mock
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import route.users as users_route  # noqa: E402
import route.visit_summary as visit_summary  # noqa: E402
import route.care_team as care_team  # noqa: E402
from services.remivox.response import builder  # noqa: E402
from services.remivox.actions import executor  # noqa: E402


class FakeConn:
    """Stands in for a Cloud SQL connection and records what it was asked."""

    def __init__(self, user_row=None, memberships=0):
        self.user_row = user_row
        self.memberships = memberships
        self.statements = []
        self.params = []

    def execute(self, statement, params=None):
        sql = str(statement)
        self.statements.append(sql)
        self.params.append(params or {})
        outer = self

        class Result:
            def fetchone(self_inner):
                if "care_team_members" in sql:
                    return (1,) if outer.memberships else None
                return outer.user_row

        return Result()


@contextlib.contextmanager
def _engine_yielding(conn):
    yield conn


def _fake_engine(conn):
    engine = mock.Mock()
    engine.connect.side_effect = lambda: _engine_yielding(conn)
    return engine


ROW = ("11111111-1111-1111-1111-111111111111", "A Name", "someone@example.com", None, "user")


class RoleIsDerivedFromMembership(unittest.TestCase):
    """users.role says "user" for all 82 rows; it has never carried this."""

    def _call_me(self, conn, cached=None):
        with mock.patch.object(users_route, "get_cloud_sql_engine", lambda: _fake_engine(conn)), \
             mock.patch.object(users_route, "get", lambda key: cached), \
             mock.patch.object(users_route, "set", lambda *a, **k: None):
            return asyncio.run(
                users_route.get_current_user_profile({"sub": "firebase-uid-1"})
            )

    def test_an_active_membership_makes_you_a_caregiver(self):
        conn = FakeConn(user_row=ROW, memberships=1)
        self.assertEqual(self._call_me(conn).role, "caregiver")

    def test_no_membership_leaves_you_a_patient(self):
        conn = FakeConn(user_row=ROW, memberships=0)
        self.assertEqual(self._call_me(conn).role, "patient")

    def test_a_cached_profile_does_not_freeze_the_role(self):
        # The regression that matters. This cache is per-process and the
        # service runs three of them, /api/users/profile fills the same key
        # with a payload that has no caregiver field at all, and the ten
        # minutes after accepting an invitation are the ten minutes this has
        # to be right in. A cached entry must not be able to answer this.
        cached = {
            "id": ROW[0],
            "email": ROW[2],
            "full_name": ROW[1],
            "phone": None,
            "db_role": "user",
        }
        conn = FakeConn(user_row=None, memberships=1)
        self.assertEqual(self._call_me(conn, cached=cached).role, "caregiver")
        self.assertTrue(
            any("care_team_members" in sql for sql in conn.statements),
            "the cached path never asked the database about membership",
        )

    def test_the_lookup_is_filtered_and_cast(self):
        conn = FakeConn(user_row=ROW, memberships=1)
        self._call_me(conn)
        sql = next(s for s in conn.statements if "care_team_members" in s)
        self.assertIn("status = 'active'", sql)
        self.assertIn("CAST(:user_id AS uuid)", sql)
        params = conn.params[conn.statements.index(sql)]
        self.assertIsInstance(params["user_id"], str)

    def test_a_revoked_or_pending_row_is_not_a_membership(self):
        # Three pending and one revoked row carry no member_user_id. Neither
        # half of the predicate can match them.
        src = inspect.getsource(users_route._is_caregiver)
        self.assertIn("status = 'active'", src)
        self.assertIn("member_user_id =", src)

    def test_accepting_drops_the_accepters_cached_profile(self):
        src = inspect.getsource(care_team.accept_care_team_invitation)
        self.assertIn('invalidate(f"user_profile:{auth_uid}")', src)

    def test_removing_drops_the_removed_members_cached_profile(self):
        src = inspect.getsource(care_team.delete_care_team_member_endpoint)
        self.assertIn("get_firebase_uid_from_uuid", src)
        self.assertIn('invalidate(f"user_profile:{removed_uid}")', src)

    def test_removal_survives_a_failed_cache_lookup(self):
        # get_firebase_uid_from_uuid raises 404 for a user that no longer
        # exists. The row is already deleted by then, so that must not be
        # reported to the caller as a failed removal.
        src = inspect.getsource(care_team.delete_care_team_member_endpoint)
        after = src.split("get_firebase_uid_from_uuid", 1)[1]
        self.assertIn("except Exception:", after)
        self.assertIn('return {"success": True}', after)

    def test_membership_creation_drops_the_profile_cache(self):
        src = inspect.getsource(visit_summary.sync_caregiver_patient_access)
        self.assertIn('invalidate(f"user_profile:{user_id}")', src)


class SnoozeHasItsOwnRefusal(unittest.TestCase):
    def test_the_message_talks_about_snoozing_not_completing(self):
        src = inspect.getsource(builder)
        start = src.index('if key == "snooze_not_due_yet":')
        body = src[start:src.index('if key == "not_due_yet":')]
        # Assert on the returned text, not the prose above it: the comment
        # explaining the fix necessarily mentions the mark-done refusal.
        spoken = "\n".join(
            line for line in body.splitlines()
            if not line.lstrip().startswith("#")
        )
        self.assertIn("nothing to snooze", spoken)
        self.assertNotIn("mark it done", spoken)
        self.assertNotIn("marked it done", spoken)

    def test_it_names_the_due_time_when_known(self):
        src = inspect.getsource(builder)
        start = src.index('if key == "snooze_not_due_yet":')
        body = src[start:src.index('if key == "not_due_yet":')]
        self.assertIn("due_local", body)

    def test_the_vox_path_uses_it(self):
        src = inspect.getsource(executor)
        start = src.index('if mode == "snooze":')
        body = src[start:start + 1600]
        self.assertIn('message_key="snooze_not_due_yet"', body)
        self.assertNotIn('message_key="not_due_yet"', body)

    def test_mark_done_still_uses_its_own(self):
        src = inspect.getsource(executor)
        start = src.index("except ReminderNotDueError as not_due:", src.index("complete_reminder(rid"))
        self.assertIn('message_key="not_due_yet"', src[start:start + 600])


class SummaryLanguageIsCurrentDesign(unittest.TestCase):
    """Report-only: a new recording does honour the switched language."""

    def test_generation_reads_the_preference_fresh(self):
        import services.ai_pipeline as pipeline
        src = inspect.getsource(pipeline)
        self.assertIn("get_user_language_preferences(user_id)", src)
        self.assertIn('visit_language', src)


if __name__ == "__main__":
    unittest.main()
