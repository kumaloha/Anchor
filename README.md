# Anchor — 经济观点准确性追踪系统

两个终端接口                                                                                                                                                                                                                                
   
  接口 1 — 分析单条 URL                                                                                                                                                                                                                       
  python run_url.py <url>                                         

  # 示例
  python run_url.py https://robinjbrooks.substack.com/p/a-massive-shock-for-global-markets
  python run_url.py https://www.youtube.com/watch?v=EbjIyoIhtc4
  python run_url.py https://weibo.com/1182426800/QoLwdfDvQ

  接口 2 — 批量读取 watchlist 新文章
  python run_monitor.py              # 全部来源，每源最多 3 条
  python run_monitor.py --dry-run    # 预览新 URL，不分析
  python run_monitor.py --source "Robin Brooks"   # 只跑指定作者
  python run_monitor.py --limit 0    # 不限条数全量跑
  python run_monitor.py --since 2026-02-01        # 自定义日期截止（默认 3 月 1 日）

  日期过滤逻辑：若 feed 条目有发布日期 → 与截止日期比较，早于截止日期的跳过；若无发布日期（如部分机构网站）→ 保留（不过滤），交给 URL 去重处理。