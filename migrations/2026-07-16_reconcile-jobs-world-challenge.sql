-- Reconcile the live `jobs` table with app/models/job.py.
--
-- The deployed table was built from an OLDER schema that used a single `story`
-- VARCHAR and a single `video_provider` enum. The current code (models +
-- routers + services) instead uses:
--     * world     ENUM(3 worlds)  NULL         (world screen removed → optional)
--     * challenge ENUM(8 slugs)   NOT NULL     (the chosen story slug)
--     * video_provider_1 / video_provider_2  ENUM('seedance','kling')
-- and treats child_name as optional (no longer collected on the form).
--
-- Symptom this fixes:
--     (1054, "Unknown column 'world' in 'field list'") on INSERT INTO jobs.
--
-- MySQL 8. No migration framework — apply by hand. Back up `jobs` first.
-- NOTE: the only existing rows are 2 old test rows (story='1', an invalid slug),
-- so they and their job_assets are removed; a fresh DB has nothing to remove.

SET SQL_SAFE_UPDATES = 0;

-- 0) Drop old test rows whose `story` isn't one of the 8 slugs (can't map to the
--    challenge ENUM). Assets go first for the job_assets → jobs FK.
DELETE FROM job_assets WHERE job_id IN (
  SELECT id FROM jobs WHERE story NOT IN (
    'dragon-eggs','magical-herb','golden-crown','fairy-rescue',
    'lost-kitten','tired-dragon','lost-puppy','snowman'
  )
);
DELETE FROM jobs WHERE story NOT IN (
  'dragon-eggs','magical-herb','golden-crown','fairy-rescue',
  'lost-kitten','tired-dragon','lost-puppy','snowman'
);

-- 1) child_name is no longer collected → allow NULL.
ALTER TABLE jobs MODIFY child_name VARCHAR(120) NULL;

-- 2) Add `world` (nullable — the world screen was removed).
ALTER TABLE jobs
  ADD COLUMN world ENUM('Mountain Peaks','Jungle Valley','Coastal Kingdom') NULL AFTER child_role;

-- 3) Replace `story` with `challenge` (the 8 story slugs). Copy any surviving
--    valid slug across, then drop the old column.
ALTER TABLE jobs
  ADD COLUMN challenge ENUM(
    'dragon-eggs','magical-herb','golden-crown','fairy-rescue',
    'lost-kitten','tired-dragon','lost-puppy','snowman'
  ) NULL AFTER world;
UPDATE jobs SET challenge = story
  WHERE story IN (
    'dragon-eggs','magical-herb','golden-crown','fairy-rescue',
    'lost-kitten','tired-dragon','lost-puppy','snowman'
  );
ALTER TABLE jobs MODIFY challenge ENUM(
    'dragon-eggs','magical-herb','golden-crown','fairy-rescue',
    'lost-kitten','tired-dragon','lost-puppy','snowman'
  ) NOT NULL;
ALTER TABLE jobs DROP COLUMN story;

-- 4) Add the dual video providers (seedance/kling) the current code writes.
--    The old single `video_provider ENUM('kie','segmind')` column is KEPT as-is
--    (nullable, left NULL) — intentionally left in place so other tooling / a
--    later backend change can still reference it; it's just unused by inserts now.
ALTER TABLE jobs
  ADD COLUMN video_provider_1 ENUM('seedance','kling') NULL AFTER photo_model;
ALTER TABLE jobs
  ADD COLUMN video_provider_2 ENUM('seedance','kling') NULL AFTER video_provider_1;

SET SQL_SAFE_UPDATES = 1;
