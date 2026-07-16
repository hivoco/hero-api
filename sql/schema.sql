-- =====================================================================
--  Hero Destini 125 — "Meri Kahani, Mera Hero"
--  Full MySQL 8 (InnoDB) schema — runnable bootstrap.
--
--  Pipeline:  selfie -> 2 photos -> 2 videos (i2v) -> stitch -> send
--  Queue:     MySQL lock-and-advance (FOR UPDATE SKIP LOCKED).
--  Recovery:  in-DB watchdog (EVENT + stored proc) — survives all-EC2-down.
--
--  Differences from the closeup blueprint already applied here:
--    * no lipsync stage
--    * 2 photos + 2 videos per job
--    * per-job provider/model/quality snapshot (admin-editable config table)
--    * child_name + parent_name (two names, not one hero_name)
--    * parent_role/child_role stored directly (no male/female -> role mapping)
--    * world enum = 3 worlds; challenge dimension (1-3) added
--    * failed_stage uses 'delivery' (closeup's 'sent' bug fixed)
--
--  Charset: utf8mb4 / utf8mb4_0900_ai_ci
-- =====================================================================

-- CREATE DATABASE IF NOT EXISTS hero_destini
--   CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci;
-- USE hero_destini;

SET NAMES utf8mb4;
SET FOREIGN_KEY_CHECKS = 0;

-- ---------------------------------------------------------------------
-- 1. users — end-user account, keyed by hashed phone
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS users (
  id               CHAR(36)     NOT NULL,
  phone_encrypted  TEXT         NOT NULL,                 -- reversible, app-side encryption
  phone_hash       CHAR(64)     NOT NULL,                 -- SHA-256 hex of normalised phone
  video_count      INT          NOT NULL DEFAULT 0,       -- denormalised job count
  created_at       TIMESTAMP    DEFAULT CURRENT_TIMESTAMP,
  updated_at       TIMESTAMP    DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (id),
  UNIQUE KEY uq_users_phone_hash (phone_hash)             -- UNIQUE already covers BTREE lookups
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

-- ---------------------------------------------------------------------
-- 2. user_verification — phone verification state (1:1 with users)
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS user_verification (
  user_id             CHAR(36)        NOT NULL,
  is_verified         TINYINT(1)      NOT NULL DEFAULT 0,
  verified_at         TIMESTAMP       NULL DEFAULT NULL,
  verification_method ENUM('otp')     NOT NULL DEFAULT 'otp',
  created_at          TIMESTAMP       DEFAULT CURRENT_TIMESTAMP,
  updated_at          TIMESTAMP       DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (user_id),
  CONSTRAINT fk_user_verification_user
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

-- ---------------------------------------------------------------------
-- 3. user_otp — WhatsApp OTP records (hashed)
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS user_otp (
  id          CHAR(36)   NOT NULL,
  user_id     CHAR(36)   NOT NULL,
  otp_hash    TEXT       NOT NULL,
  expires_at  TIMESTAMP  NOT NULL,
  attempts    INT        NOT NULL DEFAULT 0,
  is_used     TINYINT(1) NOT NULL DEFAULT 0,
  used_at     TIMESTAMP  NULL DEFAULT NULL,
  created_at  TIMESTAMP  DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (id),
  KEY idx_user_otp_user    (user_id),
  KEY idx_user_otp_expires (expires_at),
  CONSTRAINT fk_user_otp_user
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
  CONSTRAINT chk_user_otp_attempts
    CHECK (attempts BETWEEN 0 AND 10)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

-- ---------------------------------------------------------------------
-- 4. pipeline_config — live admin-edited settings (created before jobs:
--    jobs.config_id FKs into it). Only ONE row is_active=1 at a time;
--    admin "save" INSERTs a new active row and flips the old to 0 in a txn.
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS pipeline_config (
  id                  BIGINT       NOT NULL AUTO_INCREMENT,
  is_active           TINYINT(1)   NOT NULL DEFAULT 0,
  version_label       VARCHAR(32)  DEFAULT NULL,           -- e.g. 'v3-with-kling'

  -- Photo stage
  photo_provider      ENUM('segmind','kie','google') NOT NULL,
  photo_model         VARCHAR(64)                     NOT NULL,   -- 'nano-banana-pro'
  photo_quality       VARCHAR(16)                     NOT NULL,   -- '2K','1080p'
  photo_prompts       JSON                            NOT NULL,   -- { stage_key -> prompt template }
  photo_count         TINYINT                         NOT NULL DEFAULT 2,

  -- Video stage (image-to-video; two clips, providers may differ)
  video_provider_1    ENUM('seedance','kling')        NOT NULL,   -- for video_1 (U1)
  video_provider_2    ENUM('seedance','kling')        NOT NULL,   -- for video_2 (U2)
  video_model         VARCHAR(64)                     NOT NULL,   -- 'seedance-v2','kling-1.6'
  video_quality       VARCHAR(16)                     NOT NULL,   -- '720p','1080p'
  video_prompts       JSON                            NOT NULL,   -- { stage_key -> i2v prompt }
  video_duration_sec  TINYINT                         NOT NULL DEFAULT 5,

  -- Stitch stage
  stitch_pattern      JSON                            NOT NULL,   -- { stage_key -> ordered sequence }
  music_url           TEXT,                                       -- bg music S3 url
  endcard_url         TEXT,                                       -- Destini end-card S3 url

  -- Retry / TAT tunables (read by the watchdog)
  max_retry           TINYINT                         NOT NULL DEFAULT 3,
  stuck_after_minutes TINYINT                         NOT NULL DEFAULT 15,

  -- Meta
  notes               VARCHAR(255) DEFAULT NULL,
  created_by          VARCHAR(64)                     NOT NULL,
  created_at          TIMESTAMP                       DEFAULT CURRENT_TIMESTAMP,

  PRIMARY KEY (id),
  KEY idx_pipeline_config_active (is_active)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

-- NOTE: "exactly one active row" is enforced by the admin app inside the
-- atomic switch transaction (UPDATE ... is_active=0 WHERE is_active=1;
-- INSERT ... is_active=1). In-flight jobs keep their original config_id, so a
-- mid-flight admin change can't affect them.

-- ---------------------------------------------------------------------
-- 4b. vision_config — admin-editable vision model for photo validation.
--     Versioned like pipeline_config: one row status=1 (active) at a time.
--     Editing from the admin panel (POST /api/v1/change/prompt/vision)
--     inserts a new status=1 row and flips all previous rows to status=0.
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS vision_config (
  id          BIGINT       NOT NULL AUTO_INCREMENT,
  provider    VARCHAR(32)  NOT NULL,                     -- e.g. 'groq'
  model_name  VARCHAR(128) NOT NULL,                     -- e.g. 'meta-llama/llama-4-scout-17b-16e-instruct'
  prompt      TEXT         NOT NULL,                      -- system prompt for the analysis
  status      TINYINT(1)   NOT NULL DEFAULT 0,           -- 1 = active, 0 = inactive
  created_by  VARCHAR(64)  NOT NULL DEFAULT 'system',
  created_at  TIMESTAMP    DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (id),
  KEY idx_vision_config_status (status)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

-- ---------------------------------------------------------------------
-- 4c. app_settings — admin-editable runtime settings (single JSON row).
--     max_videos_per_user, unlimited_numbers, held_numbers, ...
--     Read via an in-process TTL cache (settings_service) — not per request.
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS app_settings (
  id          TINYINT      NOT NULL DEFAULT 1,
  data        JSON         NOT NULL,
  updated_by  VARCHAR(64)  DEFAULT NULL,
  updated_at  TIMESTAMP    DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

-- ---------------------------------------------------------------------
-- 5. config_audit — append-only history of every admin-panel change
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS config_audit (
  id             BIGINT       NOT NULL AUTO_INCREMENT,
  config_id      BIGINT       NOT NULL,                   -- the row that was activated
  prev_config_id BIGINT       DEFAULT NULL,               -- the row deactivated (NULL on first)
  action         ENUM('activate','rollback','edit','pause','resume') NOT NULL,
  diff           JSON         NOT NULL,                   -- {field: [old,new], ...}
  changed_by     VARCHAR(64)  NOT NULL,
  changed_at     TIMESTAMP    DEFAULT CURRENT_TIMESTAMP,
  reason         VARCHAR(255) DEFAULT NULL,
  PRIMARY KEY (id),
  KEY idx_config_audit_config     (config_id),
  KEY idx_config_audit_changed_at (changed_at),
  CONSTRAINT fk_config_audit_config
    FOREIGN KEY (config_id) REFERENCES pipeline_config(id) ON DELETE RESTRICT,
  CONSTRAINT fk_config_audit_prev
    FOREIGN KEY (prev_config_id) REFERENCES pipeline_config(id) ON DELETE RESTRICT
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

-- ---------------------------------------------------------------------
-- 6. jobs — the work queue + state machine + per-job config snapshot
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS jobs (
  id                   BIGINT       NOT NULL AUTO_INCREMENT,
  user_id              CHAR(36)     NOT NULL,

  -- Story inputs (collected on the form)
  child_name           VARCHAR(120) NOT NULL,                       -- the hero
  parent_name          VARCHAR(120) NOT NULL,
  parent_role          ENUM('father','mother') NOT NULL,           -- stored directly (no gender->role map)
  child_role           ENUM('son','daughter')  NOT NULL,
  world                ENUM('Dragon Slayer','Mountain Kingdom','Time Traveller') NOT NULL,
  challenge            TINYINT      NOT NULL,                       -- 1..3 (each world has 3)
  language             VARCHAR(32)  NOT NULL DEFAULT 'English',     -- stage key is prefixed by this
  city                 VARCHAR(120) DEFAULT NULL,                   -- location for geo analytics

  -- State machine
  status               ENUM('wait',
                            'process_stop',
                            'queued',
                            'photo_processing','photo_done',
                            'video_processing','video_done',
                            'stitching','uploaded',
                            'sent',
                            'failed') NOT NULL DEFAULT 'wait',
  retry_count          TINYINT      NOT NULL DEFAULT 0,
  locked_by            VARCHAR(64)  DEFAULT NULL,                   -- worker hostname
  locked_at            DATETIME     DEFAULT NULL,                   -- lock ts; watchdog scan key
  failed_stage         ENUM('photo','video','stitch','delivery') DEFAULT NULL,
  last_error_code      VARCHAR(64)  DEFAULT NULL,

  -- Pipeline snapshot (which config rendered this film)
  config_id            BIGINT       NOT NULL,                       -- FK -> pipeline_config.id
  photo_provider       ENUM('segmind','kie','google') DEFAULT NULL,
  photo_model          VARCHAR(64)  DEFAULT NULL,
  video_provider_1     ENUM('seedance','kling') DEFAULT NULL,
  video_provider_2     ENUM('seedance','kling') DEFAULT NULL,
  video_model          VARCHAR(64)  DEFAULT NULL,
  quality              VARCHAR(16)  DEFAULT NULL,

  -- Compliance (DPDP — per-job, version-snapshotted)
  consent_version      VARCHAR(32)  NOT NULL,
  consent_ts           DATETIME     NOT NULL,

  -- Attribution
  ip_address           VARCHAR(45)  DEFAULT NULL,                   -- client IP at submit (IPv4/IPv6)
  utm_source           VARCHAR(128) DEFAULT NULL,
  utm_medium           VARCHAR(128) DEFAULT NULL,
  utm_campaign         VARCHAR(128) DEFAULT NULL,

  created_at           TIMESTAMP    DEFAULT CURRENT_TIMESTAMP,
  updated_at           TIMESTAMP    DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,

  PRIMARY KEY (id),
  KEY idx_jobs_status_created (status, created_at),                 -- hot queue-scan index
  KEY idx_jobs_locked         (locked_at),                          -- watchdog scan path
  KEY idx_jobs_user           (user_id),
  KEY idx_jobs_config         (config_id),
  CONSTRAINT fk_jobs_user
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE RESTRICT,
  CONSTRAINT fk_jobs_config
    FOREIGN KEY (config_id) REFERENCES pipeline_config(id) ON DELETE RESTRICT,
  CONSTRAINT chk_jobs_challenge
    CHECK (challenge BETWEEN 1 AND 3)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

-- Stage / asset key composed by the worker from a job row:
--   rel = CONCAT(parent_role,'_',child_role)                 -> father_daughter
--   key = LOWER(CONCAT(language,'_',world_slug,'_challenge',challenge,'_',rel))
--         -> english_dragonslayer_challenge1_father_daughter
-- The pipeline_config JSON blobs (photo_prompts/video_prompts/stitch_pattern)
-- are keyed by this exact string.

-- ---------------------------------------------------------------------
-- 7. job_assets — per-job URLs (1:1 with jobs; written incrementally)
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS job_assets (
  job_id          BIGINT     NOT NULL,
  selfie_url      TEXT,                          -- enqueue: combined parent+child selfie
  photo_url_1     TEXT,                          -- photo stage
  photo_url_2     TEXT,                          -- photo stage
  video_url_1     TEXT,                          -- video stage (i2v from photo_1) = U1
  video_url_2     TEXT,                          -- video stage (i2v from photo_2) = U2
  audio_url       TEXT,                          -- optional voiced-name / music track
  final_video_url TEXT,                          -- stitch stage
  error           TEXT,                          -- last failure message (cap 2000 in app)
  created_at      TIMESTAMP  DEFAULT CURRENT_TIMESTAMP,
  updated_at      TIMESTAMP  DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (job_id),
  CONSTRAINT fk_job_assets_job
    FOREIGN KEY (job_id) REFERENCES jobs(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

SET FOREIGN_KEY_CHECKS = 1;

-- =====================================================================
--  WATCHDOG — runs entirely inside MySQL (no Python worker, no EC2).
--  Rewinds stuck *_processing rows to their previous *_done state and
--  permanently fails rows past max_retry. Survives all-EC2-down outages.
--
--  Every *_processing status MUST have an arm in BOTH CASE blocks below.
--  Adding a new processing state => update this proc.
-- =====================================================================
DROP PROCEDURE IF EXISTS sp_watchdog_tick;
DELIMITER //
CREATE PROCEDURE sp_watchdog_tick()
BEGIN
  DECLARE v_stuck_minutes TINYINT DEFAULT 10;     -- fallback if pipeline_config empty
  DECLARE v_max_retry     TINYINT DEFAULT 3;

  -- Read tunables from the active config (fallback to the DECLAREd defaults).
  SELECT stuck_after_minutes, max_retry
    INTO v_stuck_minutes, v_max_retry
  FROM pipeline_config
  WHERE is_active = 1
  LIMIT 1;

  -- (a) Permanently fail anything past max retries. Runs FIRST so these
  --     rows go straight to 'failed' and never get bumped by (b).
  UPDATE jobs
  SET status = 'failed',
      failed_stage = CASE status
                      WHEN 'photo_processing' THEN 'photo'
                      WHEN 'video_processing' THEN 'video'
                      WHEN 'stitching'        THEN 'stitch'
                      WHEN 'uploaded'         THEN 'delivery'   -- stuck send call
                     END,
      last_error_code = 'WATCHDOG_MAX_RETRY',
      locked_by = NULL,
      locked_at = NULL
  WHERE status IN ('photo_processing','video_processing','stitching','uploaded')
    AND locked_at < NOW() - INTERVAL v_stuck_minutes MINUTE
    AND retry_count >= v_max_retry;

  -- (b) Rewind stuck rows that still have retries left.
  UPDATE jobs
  SET status = CASE status
                WHEN 'photo_processing' THEN 'queued'
                WHEN 'video_processing' THEN 'photo_done'
                WHEN 'stitching'        THEN 'video_done'
                WHEN 'uploaded'         THEN 'uploaded'         -- keep state, just unlock + retry send
               END,
      locked_by = NULL,
      locked_at = NULL,
      retry_count = retry_count + 1,
      last_error_code = 'WATCHDOG_REWIND'
  WHERE status IN ('photo_processing','video_processing','stitching','uploaded')
    AND locked_at < NOW() - INTERVAL v_stuck_minutes MINUTE
    AND retry_count < v_max_retry;
END //
DELIMITER ;

DROP EVENT IF EXISTS evt_watchdog_tick;
CREATE EVENT evt_watchdog_tick
ON SCHEDULE EVERY 1 MINUTE
COMMENT 'Recover stuck *_processing rows; fail past max retries'
DO CALL sp_watchdog_tick();

-- =====================================================================
--  RDS PREREQUISITE (one-time): the event scheduler is OFF by default.
--    1. In the RDS parameter group set: event_scheduler = ON  (dynamic, no restart)
--    2. Verify:  SHOW VARIABLES LIKE 'event_scheduler';            -- expect ON
--    3. Grant:   GRANT EVENT ON hero_destini.* TO 'hivoco'@'%';
--    4. Confirm: SELECT event_name, status, last_executed
--                FROM information_schema.events
--                WHERE event_schema = 'hero_destini';
-- =====================================================================
