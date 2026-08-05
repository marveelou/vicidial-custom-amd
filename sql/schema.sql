-- schema.sql
-- Creates the logging table for the custom AMD engine.
-- Safe to run multiple times (CREATE TABLE IF NOT EXISTS).
--
-- Run against your existing ViciDial database, e.g.:
--   mysql -u <VARDB_user> -p<VARDB_pass> <VARDB_database> < schema.sql

CREATE TABLE IF NOT EXISTS vicidial_custom_amd_log (
    id                  BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    call_uniqueid       VARCHAR(64)     DEFAULT NULL,
    lead_id             BIGINT          DEFAULT NULL,
    campaign_id         VARCHAR(50)     DEFAULT NULL,
    extension           VARCHAR(20)     DEFAULT NULL,
    mode                ENUM('SHADOW','LIVE') NOT NULL DEFAULT 'SHADOW',

    custom_status       VARCHAR(20)     DEFAULT NULL,   -- HUMAN | MACHINE | NOTSURE
    custom_cause        VARCHAR(50)     DEFAULT NULL,    -- BEEPTONE | LONGGREETING | MAXWORDS | HUMAN | ...
    custom_run_time_ms  INT             DEFAULT NULL,
    custom_total_time_ms INT            DEFAULT NULL,
    custom_detail       VARCHAR(255)    DEFAULT NULL,

    stock_status         VARCHAR(20)    DEFAULT NULL,    -- what real AMDSTATUS was (shadow mode comparison)
    stock_cause          VARCHAR(50)    DEFAULT NULL,
    agreement            TINYINT(1)     DEFAULT NULL,    -- 1 = custom matched stock, 0 = disagreed, NULL = unknown

    recording_path       VARCHAR(255)   DEFAULT NULL,
    reviewed              TINYINT(1)    NOT NULL DEFAULT 0,   -- for a future review-dashboard pass
    reviewer_notes        VARCHAR(255)  DEFAULT NULL,

    created_at            DATETIME      NOT NULL DEFAULT CURRENT_TIMESTAMP,

    PRIMARY KEY (id),
    KEY idx_uniqueid (call_uniqueid),
    KEY idx_campaign (campaign_id),
    KEY idx_created (created_at),
    KEY idx_mode (mode)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
