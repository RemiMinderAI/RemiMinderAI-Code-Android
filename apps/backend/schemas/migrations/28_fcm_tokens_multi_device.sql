-- fcm_tokens had user_id as the whole primary key, so registering a second
-- device did not fail — it overwrote the first. A patient with a phone and a
-- tablet got reminders on one of them, a caregiver signing in on a spare
-- device knocked out their main one, and nothing anywhere reported it.
--
-- Widening the key to (user_id, fcm_token) makes one row per device. Every
-- existing row survives: user_id is currently unique, so each is already
-- unique under the wider key.
--
-- Dead-token pruning (delete_fcm_token_by_value) is what keeps this from
-- accumulating stale rows now that several per user are allowed.

ALTER TABLE public.fcm_tokens
DROP CONSTRAINT IF EXISTS fcm_tokens_pkey;

ALTER TABLE public.fcm_tokens
ADD CONSTRAINT fcm_tokens_pkey PRIMARY KEY (user_id, fcm_token);

-- Lookups are by user; the primary key's leading column serves them, but the
-- send path orders by recency so keep that covered too.
CREATE INDEX IF NOT EXISTS fcm_tokens_user_updated_idx
ON public.fcm_tokens (user_id, updated_at DESC);
