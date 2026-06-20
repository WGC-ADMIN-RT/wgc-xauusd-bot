-- WGC XAUUSD Automation Bot — Phase 1 schema
-- All timestamps stored in UTC.

CREATE TABLE IF NOT EXISTS economic_events (
    id                BIGINT AUTO_INCREMENT PRIMARY KEY,
    source            VARCHAR(32)  NOT NULL,
    source_event_id   VARCHAR(128) DEFAULT NULL,
    currency          VARCHAR(8)   NOT NULL,
    country           VARCHAR(64)  DEFAULT NULL,
    event_name        VARCHAR(255) NOT NULL,
    impact            VARCHAR(16)  NOT NULL,            -- high | medium | low
    scheduled_at_utc  DATETIME     NOT NULL,
    scheduled_at_sgt  DATETIME     NOT NULL,
    forecast          VARCHAR(64)  DEFAULT NULL,
    previous          VARCHAR(64)  DEFAULT NULL,
    actual            VARCHAR(64)  DEFAULT NULL,
    unit              VARCHAR(16)  DEFAULT NULL,
    category          VARCHAR(32)  DEFAULT NULL,        -- inflation | nfp | ...
    polarity          VARCHAR(16)  DEFAULT NULL,        -- bullish | bearish | neutral | mixed (XAUUSD)
    status            VARCHAR(24)  NOT NULL DEFAULT 'scheduled',
    -- alert bookkeeping so a cron tick never double-sends
    sent_outlook      TINYINT(1)   NOT NULL DEFAULT 0,
    sent_alert_60     TINYINT(1)   NOT NULL DEFAULT 0,
    sent_alert_15     TINYINT(1)   NOT NULL DEFAULT 0,
    sent_post_release TINYINT(1)   NOT NULL DEFAULT 0,
    created_at        DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at        DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    UNIQUE KEY uq_event (source, source_event_id),
    KEY idx_sched (scheduled_at_utc),
    KEY idx_status (status)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS intraday_analyses (
    id                  BIGINT AUTO_INCREMENT PRIMARY KEY,
    instrument          VARCHAR(16)  NOT NULL,
    analysis_time_utc   DATETIME     NOT NULL,
    analysis_time_sgt   DATETIME     NOT NULL,
    timeframe           VARCHAR(8)   NOT NULL DEFAULT 'M15',
    chart_path          VARCHAR(255) DEFAULT NULL,
    raw_market_data_json LONGTEXT    DEFAULT NULL,
    bias                VARCHAR(16)  DEFAULT NULL,
    market_condition    VARCHAR(24)  DEFAULT NULL,
    plan_json           LONGTEXT     DEFAULT NULL,
    member_message      LONGTEXT     DEFAULT NULL,
    created_at          DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    KEY idx_time (analysis_time_utc)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS bot_audit_logs (
    id            BIGINT AUTO_INCREMENT PRIMARY KEY,
    module        VARCHAR(64)  NOT NULL,
    action        VARCHAR(64)  NOT NULL,
    input_json    LONGTEXT     DEFAULT NULL,
    output_json   LONGTEXT     DEFAULT NULL,
    error_message TEXT         DEFAULT NULL,
    created_at    DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    KEY idx_module (module),
    KEY idx_created (created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
