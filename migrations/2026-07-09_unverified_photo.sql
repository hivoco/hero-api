-- Unverified-photo support (run once against the campaign DB).
--
-- Context: when the admin turns photo validation OFF, submissions skip the
-- photo check, so the selfie carries no derived roles (parent_role/child_role
-- are NULL). That NULL is the signal: once OTP is verified such jobs land in the
-- new "unverified" status instead of "queued".
--
-- This project has no migration framework (no Alembic), so apply this by hand.
-- MySQL. Back up the `jobs` table first.

-- 1) Roles become nullable (derived from the photo; absent when validation off).
ALTER TABLE jobs MODIFY COLUMN parent_role ENUM('father','mother') NULL;
ALTER TABLE jobs MODIFY COLUMN child_role  ENUM('son','daughter') NULL;

-- 2) Extend the status enum with 'unverified' (keep every existing value).
ALTER TABLE jobs MODIFY COLUMN status
  ENUM('wait','process_stop','unverified','queued',
       'photo_processing','photo_done','video_processing','video_done',
       'stitching','uploaded','sent','failed')
  NOT NULL DEFAULT 'wait';
