-- Frontend overhaul: world removed, child_name removed, and `challenge` now
-- holds the chosen STORY slug (an 8-value enum) instead of a number.
--
-- MySQL. No migration framework — apply by hand. Back up the `jobs` table first.

SET SQL_SAFE_UPDATES = 0;

-- 1) world + child_name are no longer collected → make them optional (NULL).
ALTER TABLE jobs MODIFY world ENUM('Mountain Peaks','Jungle Valley','Coastal Kingdom') NULL;
ALTER TABLE jobs MODIFY child_name VARCHAR(120) NULL;

-- 2) challenge → the 8 story slugs (an ENUM). Existing rows whose `challenge`
--    isn't one of the 8 slugs (old numeric/test data) must be removed first, or
--    the ENUM conversion rejects them. Assets are removed first for the FK.
DELETE FROM job_assets WHERE job_id IN (
  SELECT id FROM jobs WHERE challenge NOT IN (
    'dragon-eggs','magical-herb','golden-crown','fairy-rescue',
    'lost-kitten','tired-dragon','lost-puppy','snowman'
  )
);
DELETE FROM jobs WHERE challenge NOT IN (
  'dragon-eggs','magical-herb','golden-crown','fairy-rescue',
  'lost-kitten','tired-dragon','lost-puppy','snowman'
);
ALTER TABLE jobs MODIFY challenge
  ENUM('dragon-eggs','magical-herb','golden-crown','fairy-rescue',
       'lost-kitten','tired-dragon','lost-puppy','snowman') NOT NULL;

SET SQL_SAFE_UPDATES = 1;
