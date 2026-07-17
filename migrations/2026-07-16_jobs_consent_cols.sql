-- The rebuilt `jobs` table dropped the per-job consent audit columns, but we
-- still stamp them at submit (consent is captured on the details form before
-- Send OTP). Add them back.
--
-- Nullable on purpose: the rows that already exist were inserted without any
-- consent record, and back-filling them would be inventing data. Every NEW row
-- gets both values written by the submit endpoint.
--
-- MySQL 8. No migration framework — apply by hand.

ALTER TABLE jobs
  ADD COLUMN consent_version VARCHAR(32) NULL AFTER quality,
  ADD COLUMN consent_ts DATETIME NULL AFTER consent_version;
