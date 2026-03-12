-- 政策重构 v2 迁移脚本
-- policy_directives → policy_items（彻底重建表结构）
-- 重置 post_id=5,6 以便重测

-- 1. 重置 post_id=5 和 post_id=6 的数据（先删外键依赖数据）
DELETE FROM facts WHERE raw_post_id IN (5, 6);
DELETE FROM conclusions WHERE raw_post_id IN (5, 6);
DELETE FROM relationships WHERE raw_post_id IN (5, 6);
DELETE FROM policy_themes WHERE raw_post_id IN (5, 6);

-- 2. 删除旧表（policy_directives 或已迁移的 policy_items）
DROP TABLE IF EXISTS policy_directives;
DROP TABLE IF EXISTS policy_items;

-- 3. 建新表（正确 schema）
CREATE TABLE policy_items (
  id INTEGER NOT NULL PRIMARY KEY,
  raw_post_id INTEGER NOT NULL,
  policy_theme_id INTEGER,
  summary VARCHAR NOT NULL,
  policy_text VARCHAR NOT NULL,
  urgency VARCHAR NOT NULL,
  change_type VARCHAR,
  change_note VARCHAR,
  metric_value VARCHAR,
  target_year VARCHAR,
  is_hard_target BOOLEAN NOT NULL DEFAULT 0,
  created_at DATETIME NOT NULL,
  FOREIGN KEY(raw_post_id) REFERENCES raw_posts(id),
  FOREIGN KEY(policy_theme_id) REFERENCES policy_themes(id)
);
CREATE INDEX ix_policy_items_raw_post_id ON policy_items(raw_post_id);

-- 4. 重置 raw_posts 状态
UPDATE raw_posts
  SET is_processed = 0,
      assessed = 0,
      issuing_authority = NULL,
      authority_level = NULL,
      content_summary = NULL
  WHERE id IN (5, 6);
