-- Extend reminders.recurrence for interval ("every 4 hours") and custom
-- weekday ("Monday, Wednesday, Friday") schedules.
--
-- 'twice' is the value the existing code writes for twice-daily; 'twice_daily'
-- is accepted as well so either spelling round-trips.

ALTER TABLE public.reminders
DROP CONSTRAINT IF EXISTS reminders_recurrence_check;

ALTER TABLE public.reminders
ADD CONSTRAINT reminders_recurrence_check CHECK (
  recurrence IS NULL OR recurrence IN (
    'daily',
    'weekly',
    'fortnightly',
    'monthly',
    'annually',
    'once',
    'twice',
    'twice_daily',
    'interval',
    'custom'
  )
);

ALTER TABLE public.reminders
ADD COLUMN IF NOT EXISTS interval_hours integer NULL;

ALTER TABLE public.reminders
ADD COLUMN IF NOT EXISTS custom_days text[] NULL;

ALTER TABLE public.reminders
DROP CONSTRAINT IF EXISTS reminders_interval_hours_check;

ALTER TABLE public.reminders
ADD CONSTRAINT reminders_interval_hours_check CHECK (
  interval_hours IS NULL OR (interval_hours >= 1 AND interval_hours <= 24)
);
