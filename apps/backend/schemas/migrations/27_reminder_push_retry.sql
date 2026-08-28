-- A claim taken before the send meant one failed attempt silently cancelled the
-- reminder: the row stayed, every later cron run saw it as handled, and the
-- patient was never told. The row has to distinguish three states — not yet
-- attempted, attempted and still retryable, and finished (delivered or given
-- up on) — so a failure can be retried and, once out of time, reported.
--
-- The unique key on (reminder_id, scheduled_time) is deliberately unchanged.
-- It is what stops two concurrent cron runs both sending.
--
-- Retries are bounded on purpose. An unbounded retry delivers an 8 AM dose
-- alert at 10 AM, after the patient may already have taken it, and a late
-- medication prompt can cause the double dose the reminder exists to prevent.
-- Past the window the occurrence is missed, never delivered late.

ALTER TABLE public.reminder_due_push_log
ADD COLUMN IF NOT EXISTS attempts integer NOT NULL DEFAULT 1;

-- Existing rows were written under the old "claim means sent" meaning, so they
-- backfill as delivered. Anything else would make 141 historical reminders look
-- like undelivered medication alerts on the next run.
ALTER TABLE public.reminder_due_push_log
ADD COLUMN IF NOT EXISTS delivered boolean NOT NULL DEFAULT TRUE;

-- New rows start undelivered: a row now records an ATTEMPT, not a success.
ALTER TABLE public.reminder_due_push_log
ALTER COLUMN delivered SET DEFAULT FALSE;

ALTER TABLE public.reminder_due_push_log
ADD COLUMN IF NOT EXISTS last_attempt_at timestamptz NOT NULL DEFAULT NOW();

-- Set once, when we stop trying. Also the flag that keeps the "not delivered"
-- log line to exactly one per missed occurrence instead of one per cron run.
ALTER TABLE public.reminder_due_push_log
ADD COLUMN IF NOT EXISTS abandoned_at timestamptz;

-- The retry sweep looks for unfinished rows; without this it scans the table.
CREATE INDEX IF NOT EXISTS reminder_due_push_log_unfinished_idx
ON public.reminder_due_push_log (scheduled_time)
WHERE delivered = FALSE AND abandoned_at IS NULL;
