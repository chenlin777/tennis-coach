"""Build a private, offline video review page from local dataset records."""

import csv
import html
import json
from pathlib import Path
from urllib.parse import quote, urlsplit
import webbrowser
import argparse

ROOT = Path(__file__).resolve().parents[1]
DATASET = ROOT / "local" / "dataset"


def escape(value):
    return html.escape(str(value or ""), quote=True)


def status_label(value):
    return {
        "unreviewed": "未标注", "in_progress": "标注中", "reviewed": "已标注",
        "pending": "边界待检查", "accepted": "边界已确认", "needs_recut": "需要重新剪辑",
        "not_attempted": "尚未尝试", "blocked_bot_verification": "需要平台登录验证",
        "blocked_http_403": "平台拒绝访问", "blocked_fresh_cookies_required": "需要新的访问凭据",
    }.get(value, value)


def local_link(path):
    absolute = (ROOT / path).resolve()
    if not absolute.is_relative_to((ROOT / "local").resolve()):
        raise ValueError("Review links must point inside local/.")
    return "../" + quote(absolute.relative_to(ROOT / "local").as_posix(), safe="/")


def build_page():
    DATASET.mkdir(parents=True, exist_ok=True)
    cards = []
    for number, path in enumerate(sorted((DATASET / "items").glob("*.json")), 1):
        record = json.loads(path.read_text(encoding="utf-8"))
        label = json.loads((ROOT / record["label_path"]).read_text(encoding="utf-8"))
        raw_path = ROOT / record["parent_raw_path"]
        source = json.loads(raw_path.with_name("source.json").read_text(encoding="utf-8"))
        cards.append(f'''<article>
<h2>第 {number:02d} 段 · {escape(source.get('title') or record['source_id'])}</h2>
<p>原片 {record['start_seconds']:.3f}–{record['end_seconds']:.3f} 秒 · 短片 {record['encoded_duration']:.2f} 秒</p>
<video controls preload="none" playsinline src="{escape(local_link(record['clip_path']))}"></video>
<p class="state">标签：{escape(status_label(label['review']['status']))} · {escape(status_label(label['boundary_review']))}</p>
<p class="id">片段代号：{escape(record['clip_id'])}</p>
<p><a href="{escape(local_link(record['parent_raw_path']))}">打开原片</a> ·
<a href="{escape(local_link(record['label_path']))}">查看标签记录</a></p>
<p>讲解时请说：第 {number:02d} 段，短片第几秒，看到了什么，为什么这样判断，优先怎么改。</p>
</article>''')
    candidates = []
    candidate_file = DATASET / "candidates.csv"
    if candidate_file.exists():
        with candidate_file.open(encoding="utf-8-sig", newline="") as stream:
            for row in csv.DictReader(stream):
                url = row.get("url", "")
                if urlsplit(url).scheme not in {"http", "https"}:
                    continue
                candidates.append(f'''<li><a href="{escape(url)}" target="_blank" rel="noopener noreferrer">{escape(row.get('candidate_id'))} · {escape(row.get('title'))}</a>
<small>{escape(row.get('platform'))} · 下载状态：{escape(status_label(row.get('download_status') or 'not_attempted'))}</small></li>''')
    output = f'''<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Tennis Coach · 本地视频复看</title>
<style>
body{{margin:0;background:#f1f5f0;color:#193d37;font:16px/1.65 system-ui,sans-serif}}
main{{max-width:1040px;margin:auto;padding:28px 20px}}h1{{font-size:30px}}h2{{font-size:21px}}
article,section{{background:#fff;border:1px solid #d5e0d6;border-radius:14px;padding:22px;margin:22px 0}}
video{{display:block;width:100%;max-height:560px;background:#172821;border-radius:8px}}
a{{color:#15654f}}small{{display:block;color:#586f65}}li{{margin-bottom:12px}}.id{{overflow-wrap:anywhere;font-size:13px}}
.state{{color:#75602c}}details{{margin-top:30px}}.note{{border-left:4px solid #8ca891;padding-left:18px}}
</style></head><body><main>
<h1>网球视频 · 本地复看</h1>
<p>已整理 {len(cards)} 个本地短片。视频和标签保存在这台电脑；此页不上传文件，也不自动访问外部视频。</p>
<section><h2>播放视频，口述你的判断</h2>
<p>先检查片头片尾有没有切断一次挥拍。可以从一个最想讲的片段开始，不必一次填完所有项目。</p>
<p>观察顺序：眼睛跟球 → 辅助手 → 击球点 → 随挥 → 屈膝与身体高度 → 移动与击球位置。</p>
<p class="note">整体可以判断“好、需改进、无法判断、依情境”。每一项仍分别判断；看不清或被遮盖的地方不算动作错误。这一页用于复看，标签由我们根据你的讲解整理，尚不会自动保存你的评价。</p>
<p>加入新视频或更新标签后，重新运行 start-dataset.cmd 刷新本页。</p></section>
{''.join(cards) or '<p>还没有短片。把视频拖到 prepare-clips.cmd，再重新打开此页。</p>'}
<details><summary>外部候选清单（{len(candidates)} 条，查看来源与下载状态）</summary>
<p>这些候选仅通过标题和描述初筛，没有自动标为好或不好。链接不代表已经下载到本地；当前优先使用自有或同意提供的视频。</p>
<ul>{''.join(candidates)}</ul></details>
</main></body></html>'''
    path = DATASET / "review.html"
    path.write_text(output, encoding="utf-8")
    return path


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--open", action="store_true")
    args = parser.parse_args()
    page = build_page()
    print(page)
    if args.open:
        webbrowser.open(page.as_uri())
