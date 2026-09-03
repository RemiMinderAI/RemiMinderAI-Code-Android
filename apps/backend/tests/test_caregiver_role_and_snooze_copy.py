"""Where the caregiver role comes from, and what a refused snooze says."""

import inspect, os, sys, unittest
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import route.users as users_route  # noqa: E402
import route.visit_summary as visit_summary  # noqa: E402
from services.remivox.response import builder  # noqa: E402
from services.remivox.actions import executor  # noqa: E402


class RoleIsDerivedFromMembership(unittest.TestCase):
    """users.role says "user" for all 82 rows; it has never carried this."""

    def test_the_derivation_exists(self):
        src = inspect.getsource(users_route._is_caregiver)
        self.assertIn("care_team_members", src)
        self.assertIn("status = 'active'", src)
        self.assertIn("member_user_id = :user_id", src)

    def test_me_uses_it_on_the_uncached_path(self):
        src = inspect.getsource(users_route.get_current_user_profile)
        self.assertIn("_is_caregiver(conn, user_id)", src)
        self.assertIn('api_role = "caregiver"', src)

    def test_the_cached_path_agrees(self):
        # A ten-minute cache that still said "patient" would undo the fix for
        # exactly the window that matters after accepting an invitation.
        src = inspect.getsource(users_route.get_current_user_profile)
        self.assertIn('cached.get("is_caregiver")', src)
        self.assertIn('"is_caregiver": caregiver', src)

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
