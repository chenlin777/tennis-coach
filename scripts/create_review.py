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
CRITERIA = {
    "eyes_follow_ball": "眼睛跟球", "non_dominant_arm": "辅助手",
    "contact_in_front": "击球点在身前", "follow_through": "随挥",
    "knee_bend_and_body_height": "屈膝与身体高度", "movement_and_positioning": "移动与击球位置",
}
JUDGMENTS = {"good": "好", "needs_improvement": "需改进", "unobservable": "无法判断", "context_dependent": "依情境"}


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


def review_details(review, *, assistant=False):
    if not review:
        return ""
    parts = [f'<div class="assessment"><h3>{"助手初标 · 待教练复核" if assistant else "教练判断"}</h3>',
             f'<p>{escape(review.get("summary"))}</p>']
    if review.get("highest_priority"):
        priority = review["highest_priority"]
        parts.append(f'<p><strong>优先关注：</strong>{escape(CRITERIA.get(priority, priority))}</p>')
    for key, criterion in review.get("criteria", {}).items():
        judgment = criterion.get("judgment")
        if judgment is None:
            continue
        parts.append(f'<details class="criterion"><summary>{escape(CRITERIA.get(key, key))} · '
                     f'<span class="{escape(judgment)}">{escape(JUDGMENTS.get(judgment, judgment))}</span></summary>')
        confidence = {"high": "高", "medium": "中", "low": "低"}.get(criterion.get("confidence"))
        if confidence:
            parts.append(f'<small>初步判断把握：{confidence}（不是模型概率）</small>')
        if criterion.get("reason_unobservable"):
            parts.append(f'<p>{escape(criterion["reason_unobservable"])}</p>')
        for observation in criterion.get("observations", []):
            start, end = observation.get("start_seconds"), observation.get("end_seconds")
            if isinstance(start, (int, float)) and isinstance(end, (int, float)):
                parts.append(f'<button type="button" class="seek" data-time="{escape(start)}">'
                             f'定位 {start:.2f}–{end:.2f} 秒</button>')
            parts.append(f'<p><strong>画面：</strong>{escape(observation.get("visible_fact"))}</p>')
            interpretation = observation.get("interpretation" if assistant else "coach_interpretation")
            if interpretation:
                parts.append(f'<p><strong>{"初步解释" if assistant else "教练解释"}：</strong>{escape(interpretation)}</p>')
            if observation.get("suggestion"):
                parts.append(f'<p><strong>建议：</strong>{escape(observation["suggestion"])}</p>')
        parts.append('</details>')
    if assistant:
        parts.append(f'<p class="muted">查看方式：{escape(review.get("review_method"))}<br>'
                     f'观察范围：{escape(review.get("coverage_notes"))}</p>')
    parts.append('</div>')
    return ''.join(parts)


def build_page(*, external_only=False):
    DATASET.mkdir(parents=True, exist_ok=True)
    cards = []
    assistant_count = 0
    coach_count = 0
    for number, path in enumerate(sorted((DATASET / "items").glob("*.json")), 1):
        record = json.loads(path.read_text(encoding="utf-8"))
        label = json.loads((ROOT / record["label_path"]).read_text(encoding="utf-8"))
        raw_path = ROOT / record["parent_raw_path"]
        source = json.loads(raw_path.with_name("source.json").read_text(encoding="utf-8"))
        if external_only and source.get("platform") == "local":
            continue
        draft = label.get("assistant_review")
        assistant_count += bool(draft)
        coach_count += label['review']['status'] == 'reviewed'
        review_id = label.get('curation', {}).get('review_id') or f'clip-{number:02d}'
        needs_improvement = bool(draft and any(c.get('judgment') == 'needs_improvement' for c in draft.get('criteria', {}).values()))
        source_url = label.get('source', {}).get('url') or source.get('url', '')
        source_link = (f'<a href="{escape(source_url)}" target="_blank" rel="noopener noreferrer">原始来源</a> · '
                       if source_url and urlsplit(source_url).scheme in {'http', 'https'} else '')
        cards.append(f'''<article id="{escape(review_id)}" data-draft="{int(bool(draft))}" data-improve="{int(needs_improvement)}">
<h2>{escape(review_id)} · {escape(source.get('title') or record['source_id'])}</h2>
<p>原片 {record['start_seconds']:.3f}–{record['end_seconds']:.3f} 秒 · 短片 {record['encoded_duration']:.2f} 秒</p>
<p class="muted">{escape(label.get('curation', {}).get('content_notes', ''))}</p>
<video controls preload="none" playsinline src="{escape(local_link(record['clip_path']))}"></video>
<p class="state">教练标签：{escape(status_label(label['review']['status']))} · 助手初标：{'已完成，待复核' if draft else '暂无'} · {escape(status_label(label['boundary_review']))}</p>
<p class="id">片段代号：{escape(record['clip_id'])}</p>
<p><a href="{escape(local_link(record['parent_raw_path']))}">打开原片</a> ·
{source_link}<a href="{escape(local_link(record['label_path']))}">查看标签记录</a></p>
{review_details(label['review']) if label['review']['status'] != 'unreviewed' else ''}
{review_details(draft, assistant=True)}
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
.criterion{{margin:10px 0;border-top:1px solid #e2e9e2;padding:10px 0}}summary,button{{cursor:pointer}}
.assessment{{margin-top:20px}}.assessment p{{margin:8px 0}}.muted{{color:#617367;font-size:14px}}
.good{{color:#16654c}}.needs_improvement{{color:#974719}}.unobservable{{color:#687066}}
button,select{{font:inherit;padding:5px 9px;margin:5px 10px 5px 0}}article[hidden]{{display:none}}
</style></head><body><main>
<h1>网球视频 · 本地复看</h1>
<p>已整理 {len(cards)} 个本地短片 · {assistant_count} 段有助手初标 · {coach_count} 段经教练确认。视频和标签保存在这台电脑；此页不上传文件，也不自动访问外部视频。</p>
<section><h2>查看初步判断与对应动作</h2>
<p>展开每一项可查看依据，点击时间定位到短片中的动作。助手初标来自按教学规则查看视频的过程，尚未经过教练确认；当前工具本身不会自动分析新视频。</p>
<p>观察顺序：眼睛跟球 → 辅助手 → 击球点 → 随挥 → 屈膝与身体高度 → 移动与击球位置。</p>
<p class="note">好、需改进、无法判断、依情境分别记录。看不清的地方不算动作错误。初标与教练标签分开保存，不能当作教练已确认的训练答案。</p>
<p>加入新视频或更新标签后，重新运行 start-dataset.cmd 刷新本页。页面用于复看，暂不保存在线编辑。</p>
<label>显示 <select id="filter"><option value="all">全部片段</option><option value="draft">有助手初标</option><option value="improve">初标中有改进建议</option></select></label>
<label>播放速度 <select id="rate"><option value="1">原视频速度</option><option value="0.5">半速</option><option value="0.25">四分之一速度</option></select></label></section>
{''.join(cards) or '<p>还没有短片。把视频拖到 prepare-clips.cmd，再重新打开此页。</p>'}
<details><summary>外部候选清单（{len(candidates)} 条，查看来源与下载状态）</summary>
<p>候选链接不代表已经下载到本地，也不代表对动作好坏的判断。成功下载的视频仍需筛选可见的击球内容。</p>
<ul>{''.join(candidates)}</ul></details>
</main><script>
const videos = [...document.querySelectorAll('video')];
document.getElementById('rate').addEventListener('change', event => {{videos.forEach(video => {{
  video.defaultPlaybackRate = Number(event.target.value);
  video.playbackRate = Number(event.target.value);
}});}});
videos.forEach(video => video.addEventListener('play', () => {{videos.forEach(other => {{if (other !== video) other.pause();}});}}));
document.querySelectorAll('.seek').forEach(button => button.addEventListener('click', () => {{
  const video = button.closest('article').querySelector('video');
  const seek = () => {{video.playbackRate = Number(document.getElementById('rate').value); video.currentTime = Number(button.dataset.time); video.scrollIntoView({{block:'center', behavior:'smooth'}});}};
  if (video.readyState >= 1) seek(); else {{video.addEventListener('loadedmetadata', seek, {{once:true}}); video.load();}}
}}));
document.getElementById('filter').addEventListener('change', event => {{
  document.querySelectorAll('article').forEach(card => {{
    card.hidden = event.target.value === 'draft' ? card.dataset.draft !== '1' : event.target.value === 'improve' ? card.dataset.improve !== '1' : false;
    if (card.hidden) card.querySelector('video').pause();
  }});
}});
</script></body></html>'''
    path = DATASET / ("new-materials.html" if external_only else "review.html")
    path.write_text(output, encoding="utf-8")
    return path


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--open", action="store_true")
    args = parser.parse_args()
    page = build_page()
    build_page(external_only=True)
    print(page)
    if args.open:
        webbrowser.open(page.as_uri())
