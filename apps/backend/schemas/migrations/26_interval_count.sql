-- "Every N of the base period" — one column that composes with every existing
-- recurrence rather than adding interval_days, interval_weeks and so on:
--
--   daily          + interval_count=2  -> every other day
--   daily          + interval_count=3  -> every 3 days
--   weekly         + interval_count=2  -> every other week
--   custom(monday) + interval_count=2  -> every other Monday
--
-- DEFAULT 1 means every existing row keeps exactly its current behaviour.
-- The upper bound stops a mistranscribed number ("every 300 days") creating a
-- reminder years out.

ALTER TABLE public.reminders
ADD COLUMN IF NOT EXISTS interval_count integer NOT NULL DEFAULT 1;

ALTER TABLE public.reminders
DROP CONSTRAINT IF EXISTS reminders_interval_count_check;

ALTER TABLE public.reminders
ADD CONSTRAINT reminders_interval_count_check CHECK (
  interval_count >= 1 AND interval_count <= 60
);
