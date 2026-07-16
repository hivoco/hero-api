-- Remember that a user has accepted the campaign T&C / 18+ declaration, so a
-- returning user (same mobile number) gets the consent box pre-ticked on the
-- details form instead of re-reading it every visit.
--
-- Named `tc_accepted` (not `t&c_accepted`) — `&` isn't a plain SQL identifier
-- and would need backquoting at every use site.
--
-- MySQL 8. No migration framework — apply by hand.

ALTER TABLE users
  ADD COLUMN tc_accepted TINYINT(1) NOT NULL DEFAULT 0 AFTER video_count;
