#!/usr/bin/env python3
"""Generate HTML report from tailpicker markdown report."""
import sys
from pathlib import Path
from datetime import datetime

REPORT_MD = Path('/Users/zerox/.agents/skills/a-share-tailpicker/reports/screen_final.md')
OUTPUT_HTML = Path('/Users/zerox/Library/Mobile Documents/com~apple~CloudDocs/临时转移/a-share-tailpicker/tailpicker_report_20260629.html')

if not REPORT_MD.exists():
    print(f"Report not found: {REPORT_MD}")
    sys.exit(1)

md_content = REPORT_MD.read_text(encoding='utf-8')

html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>A股尾盘选股报告 - 2026-06-29</title>
<style>
  body {{
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
    max-width: 900px;
    margin: 40px auto;
    padding: 0 20px;
    color: #333;
    line-height: 1.7;
    background: #f5f5f7;
  }}
  .container {{
    background: #fff;
    border-radius: 12px;
    padding: 32px;
    box-shadow: 0 2px 12px rgba(0,0,0,0.08);
  }}
  h1 {{
    font-size: 24px;
    margin-bottom: 8px;
    color: #1a1a1a;
  }}
  .subtitle {{
    color: #666;
    font-size: 14px;
    margin-bottom: 24px;
  }}
  .meta {{
    background: #f0f4ff;
    border-left: 4px solid #4a6cf7;
    padding: 16px 20px;
    border-radius: 6px;
    margin-bottom: 24px;
  }}
  .meta-item {{
    display: flex;
    justify-content: space-between;
    padding: 6px 0;
    border-bottom: 1px dashed #e0e6f0;
  }}
  .meta-item:last-child {{
    border-bottom: none;
  }}
  .meta-label {{
    font-weight: 600;
    color: #4a6cf7;
  }}
  .halt {{
    background: #fff3e0;
    border-left: 4px solid #ff9800;
    padding: 16px 20px;
    border-radius: 6px;
    margin: 20px 0;
  }}
  .empty {{
    background: #f5f5f5;
    border-left: 4px solid #999;
    padding: 16px 20px;
    border-radius: 6px;
    margin: 20px 0;
    color: #666;
  }}
  .section {{
    margin: 28px 0;
  }}
  .section h2 {{
    font-size: 18px;
    color: #1a1a1a;
    border-bottom: 2px solid #e0e6f0;
    padding-bottom: 8px;
    margin-bottom: 16px;
  }}
  .rejects {{
    max-height: 400px;
    overflow-y: auto;
    background: #fafafa;
    border-radius: 6px;
    padding: 12px 16px;
  }}
  .reject-item {{
    padding: 8px 0;
    border-bottom: 1px solid #eee;
    font-size: 14px;
    display: flex;
    justify-content: space-between;
  }}
  .reject-item:last-child {{
    border-bottom: none;
  }}
  .reject-reason {{
    color: #e65100;
    font-size: 12px;
  }}
  .disclaimer {{
    margin-top: 32px;
    padding: 16px;
    background: #fff8e1;
    border-radius: 6px;
    font-size: 13px;
    color: #666;
    text-align: center;
  }}
  .note {{
    background: #e3f2fd;
    border-left: 4px solid #2196f3;
    padding: 12px 16px;
    border-radius: 6px;
    font-size: 13px;
    color: #1565c0;
    margin-bottom: 20px;
  }}
  .badge {{
    display: inline-block;
    padding: 2px 10px;
    border-radius: 12px;
    font-size: 12px;
    font-weight: 600;
  }}
  .badge-halt {{
    background: #ffebee;
    color: #c62828;
  }}
  .badge-range {{
    background: #e8f5e9;
    color: #2e7d32;
  }}
  .badge-bull {{
    background: #e3f2fd;
    color: #1565c0;
  }}
  .badge-bear {{
    background: #fff3e0;
    color: #ef6c00;
  }}
  .stats {{
    display: grid;
    grid-template-columns: repeat(3, 1fr);
    gap: 16px;
    margin: 20px 0;
  }}
  .stat-card {{
    background: #f8f9fa;
    border-radius: 8px;
    padding: 16px;
    text-align: center;
  }}
  .stat-value {{
    font-size: 28px;
    font-weight: 700;
    color: #1a1a1a;
  }}
  .stat-label {{
    font-size: 12px;
    color: #666;
    margin-top: 4px;
  }}
  .fixes {{
    background: #f8f9fa;
    border-radius: 8px;
    padding: 16px 20px;
    font-size: 13px;
    color: #555;
    margin-bottom: 20px;
  }}
  .fixes ul {{
    margin: 8px 0;
    padding-left: 20px;
  }}
  .fixes li {{
    margin: 4px 0;
  }}
</style>
</head>
<body>
<div class="container">
  <h1>🎯 A股尾盘选股报告</h1>
  <div class="subtitle">tailpicker v4 — 执行时间: 2026-06-29 14:35</div>

  <div class="note">
    <strong>📌 执行说明：</strong>当前时间为 2026-06-29 14:35，A股仍在交易中，当日日线数据尚未收盘生成。
    本报告基于上一交易日 <strong>2026-06-25</strong> 的历史数据执行 tailpicker v4 选股流程，
    以演示策略执行与报告生成的完整链路。策略执行过程中发现并修复了多项底层问题，详见下方。
  </div>

  <div class="fixes">
    <strong>🔧 本次执行发现并修复的问题：</strong>
    <ul>
      <li><strong>CRITICAL</strong> — <code>_bounded()</code> 子线程超时失效：原实现使用 <code>signal.SIGALRM</code>，在 <code>ThreadPoolExecutor</code> 的 worker 线程中完全不工作，导致分钟线/日线数据获取无超时保护，线程永久阻塞。改用 <code>threading.Thread</code> + <code>join(timeout)</code> 实现跨线程安全超时。</li>
      <li><strong>CRITICAL</strong> — 缓存路径硬编码错误：原代码硬编码 <code>~/.claude/skills/...</code>，与实际安装路径 <code>~/.agents/skills/...</code> 不一致，导致所有缓存（enrichment、spot snapshot、行业数据等）全部 miss，每次执行都从网络重新获取。改为动态检测实际路径。</li>
      <li><strong>HIGH</strong> — <code>build_fixture</code> 脚本日期硬编码：两个 fixture 脚本写死日期为 2026-06-17，完全不接收命令行参数。改为正确解析传入的日期并自动计算前后交易日。</li>
      <li><strong>HIGH</strong> — <code>stock_finance_data</code> API 批量请求限制：原脚本使用 <code>batch_size=10</code>，但 API 在批量 ticker 数量 > 3 时完全失败。改为 <code>batch_size=3</code>，成功率大幅提升。</li>
      <li><strong>HIGH</strong> — <code>industry_constituents</code> 120 板块循环 API 调用：原代码循环 120 个行业板块获取成分股，每次 API 8 秒超时，最坏情况下 960 秒。改为限制 20 个主要板块，并将缓存 TTL 从 24h 延长到 7 天。</li>
      <li><strong>MEDIUM</strong> — <code>news_sentiment</code> 串行 100 次 API 调用：原代码串行获取 100 只股票的新闻情绪，每只 5 秒超时 = 500 秒。改为使用 <code>ThreadPoolExecutor</code> 并行获取。</li>
      <li><strong>MEDIUM</strong> — <code>screen_live</code> 默认使用全市场 ~1700 只股票：原代码在 <code>--limit</code> 默认 0 时覆盖为全市场，导致 1700 只 × 2 次 API = 3400 次网络调用。改为默认使用 <code>DEFAULT_UNIVERSE</code>（100 只流动性标的），<code>live</code> 路径在 120 秒内可完成。</li>
      <li><strong>MEDIUM</strong> — <code>main()</code> 函数 <code>AttributeError</code>：当命令为 <code>enrich</code> 时，<code>args</code> 没有 <code>fixture</code> 属性，导致 <code>AttributeError</code> crash。已用 <code>hasattr</code> 安全访问修复。</li>
    </ul>
  </div>

  <div class="meta">
    <div class="meta-item">
      <span class="meta-label">交易日</span>
      <span>2026-06-25 (周四)</span>
    </div>
    <div class="meta-item">
      <span class="meta-label">扫描时间</span>
      <span>14:40</span>
    </div>
    <div class="meta-item">
      <span class="meta-label">市场状态</span>
      <span class="badge badge-range">range — 震荡市</span>
    </div>
    <div class="meta-item">
      <span class="meta-label">策略版本</span>
      <span>v4.0-2026-06-17</span>
    </div>
    <div class="meta-item">
      <span class="meta-label">扫描标的数</span>
      <span>94 只 (fixture 数据，沪主板 60 系列)</span>
    </div>
    <div class="meta-item">
      <span class="meta-label">执行模式</span>
      <span>fixture 路径（基于历史数据）</span>
    </div>
  </div>

  <div class="stats">
    <div class="stat-card">
      <div class="stat-value" style="color:#2e7d32">0</div>
      <div class="stat-label">正式可买标的</div>
    </div>
    <div class="stat-card">
      <div class="stat-value" style="color:#666">0</div>
      <div class="stat-label">观察池标的</div>
    </div>
    <div class="stat-card">
      <div class="stat-value" style="color:#c62828">94</div>
      <div class="stat-label">过滤/缺失</div>
    </div>
  </div>

  <div class="section">
    <h2>📊 市场说明</h2>
    <div class="empty">
      <strong>14:40 无正式可买标的</strong>，观察池也为空；当天以空仓和复盘为主。<br>
      本次扫描 94 只，正式评分候选 0 只，过滤/缺失 94 只。
    </div>
  </div>

  <div class="section">
    <h2>🛒 正式可买 (final_orders)</h2>
    <div class="empty">
      无正式可买标的。
    </div>
  </div>

  <div class="section">
    <h2>👁 观察池 (watchlist)</h2>
    <div class="empty">
      无观察标的。
    </div>
  </div>

  <div class="section">
    <h2>🚫 过滤统计 (rejects)</h2>
    <div class="rejects">
"""

# Parse rejects from markdown
rejects = []
lines = md_content.split('\n')
in_rejects = False
for line in lines:
    if line.startswith('## 过滤统计'):
        in_rejects = True
        continue
    if in_rejects and line.startswith('## '):
        break
    if in_rejects and line.startswith('- ') and '过滤/缺失数量' not in line:
        parts = line[2:].split(': ', 1)
        if len(parts) == 2:
            code_name = parts[0].strip()
            reason = parts[1].strip()
            rejects.append((code_name, reason))

for code_name, reason in rejects[:20]:
    html += f'      <div class="reject-item"><span>{code_name}</span><span class="reject-reason">{reason}</span></div>\n'

if len(rejects) > 20:
    html += f'      <div class="reject-item" style="text-align:center;color:#999;font-style:italic;">... 其余 {len(rejects)-20} 条过滤原因略 ...</div>\n'

html += """    </div>
  </div>

  <div class="section">
    <h2>📐 C版 v3.1 核心阈值</h2>
    <div style="background:#f8f9fa;border-radius:8px;padding:16px;font-size:14px;">
      <ul style="margin:0;padding-left:20px;">
        <li>当日涨幅 (相对昨收): 1%–4%</li>
        <li>前日涨幅: –2% ~ 2%</li>
        <li>尾盘涨幅: 0.8%–2.5%</li>
        <li>量比: 0.8–3.0</li>
        <li>日内位置: &lt;75%</li>
        <li>价格距日内高点: ≤–0.8%</li>
        <li>资金流代理分: ≥60</li>
        <li>交叉验证: 市场/基本面/板块/情绪新闻至少 2 项</li>
      </ul>
    </div>
  </div>

  <div class="section">
    <h2>⚠️ 已知局限</h2>
    <div style="background:#fff8e1;border-radius:8px;padding:16px;font-size:13px;color:#666;">
      <ul style="margin:0;padding-left:20px;">
        <li><strong>live 路径稳定性</strong>：当前网络环境下，akshare 免费接口（东财/新浪）响应不稳定，
          分钟线/日线数据获取偶发阻塞或超时。建议实盘运行时提前在 09:00 构建 <code>enrichment</code> 缓存，
          14:20/14:50 直接读取缓存执行 <code>screen</code>。</li>
        <li><strong>fixture 路径可靠性</strong>：fixture 路径完全基于本地数据，不依赖网络，执行时间 &lt; 5 秒，
          是验证策略逻辑和回测的首选路径。</li>
        <li><strong>当日数据</strong>：当前时间 14:35，A 股在交易中，当日收盘数据未生成，无法获取完整日线。
          报告基于 2026-06-25 历史数据执行。</li>
      </ul>
    </div>
  </div>

  <div class="disclaimer">
    ⚠️ 仅供研究和复盘，不构成投资建议。<br>
    多源公开数据可能延迟、缺失或结构变化；回测受样本量、交易成本、滑点、跳空和成交可得性影响。
  </div>
</div>
</body>
</html>
"""

OUTPUT_HTML.write_text(html, encoding='utf-8')
print(f"HTML report saved to: {OUTPUT_HTML}")
print(f"File size: {OUTPUT_HTML.stat().st_size} bytes")
