-- Idempotency store for RevenueCat webhook deliveries.
--
-- RevenueCat retries on any non-2xx, and can redeliver on its own schedule, so
-- the same event id must be safe to receive twice. The primary key IS the
-- idempotency guarantee: a duplicate insert conflicts and the handler skips.

CREATE TABLE IF NOT EXISTS public.revenuecat_webhook_events (
  event_id     text PRIMARY KEY,
  event_type   text NULL,
  app_user_id  text NULL,
  received_at  timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS revenuecat_webhook_events_received_at_idx
  ON public.revenuecat_webhook_events (received_at DESC);
