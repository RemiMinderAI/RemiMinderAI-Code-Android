-- Vox usage quotas.
--
-- Trial reuses users.remivox_interaction_count (a lifetime counter, which is
-- exactly right for "15 uses total during the trial").
--
-- Premium needs a rolling 30-day window, so it gets its own anchored counter.
-- The anchor is stored rather than derived so the window rolls forward in whole
-- 30-day steps: a user who does not open the app for two months keeps their
-- original reset day instead of silently shifting it.

ALTER TABLE public.users
ADD COLUMN IF NOT EXISTS vox_period_start timestamptz NULL;

ALTER TABLE public.users
ADD COLUMN IF NOT EXISTS vox_period_count integer NOT NULL DEFAULT 0;

ALTER TABLE public.users
DROP CONSTRAINT IF EXISTS users_vox_period_count_check;

ALTER TABLE public.users
ADD CONSTRAINT users_vox_period_count_check CHECK (vox_period_count >= 0);
