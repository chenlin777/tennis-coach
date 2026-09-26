# 助手初标

教练可以授权助手先看视频、按已提供的方法做初步判断，再在有空时集中复核。这能减少逐段口述的工作量。助手初标保存在 `assistant_review`，教练确认的判断保存在 `review`；两者在页面中分别显示。未确认的初标不是训练或评测的标准答案。

当前分析由助手查看带时间的画面序列完成。`import_assistant_reviews.py` 只校验和保存已有结果，不运行视觉模型；`create_review.py` 只生成本机复看页。安装软件或导入视频不会自动获得教学评价。

## 教练规则 v1

这些是本项目教练已提供的观察方法，不是对所有打法的统一技术标准。

| 项目 | 观察要求 | 判断边界 |
| --- | --- | --- |
| 眼睛跟球 | 眼睛持续跟踪来球和击球过程 | 头转向球不等于已证明视线跟球；脸部太小、背面或打码时记无法判断。 |
| 辅助手 | 准备时举起、指向球，向前挥拍时回收；“向后拉、肘击”是教练的动作提示 | 区分非持拍侧与画面左右；先确认持拍手、正反手、准备和挥拍阶段。 |
| 击球点在身前 | 检查接触前后球、拍与身体的可见关系 | 教练尚未给出统一距离或参考轴；不能凭单机位猜厘米数或三维距离。接触帧缺失则保留未知。 |
| 随挥 | 持拍手向对侧肩方向完成随挥，**或**持拍侧肩膀从后方转到前方 | 保留“或”；不要求所有来球都以同一拍头位置结束。 |
| 屈膝与身体高度 | 观察膝部屈伸、站姿高度以及击球任务 | 画面中的身体高度不是测得的重心。教练的少移动打墙示范中，屈膝少可以接受。 |
| 移动与击球位置 | 来球前调整，给自己合适的击球空间 | 移动少本身不是错误。判断需要受迫伸手、拥挤或失衡等具体画面，并结合训练意图。 |

每项使用 `good`、`needs_improvement`、`unobservable` 或 `context_dependent`，不用一个总分替代六项判断。片段中可以同时有优点和改进点。职业选手身份、视频标题里的水平等级、上传者自评都不是动作证据。

## 证据与观察范围

先浏览整段，定位正手和转场，再密集查看代表性动作的准备、挥拍、接触附近和随挥。抽帧可能错过球拍接触球的瞬间，不能声称已经逐帧看完或评判每一球。保留 `review_method`、`coverage_notes` 与 `evidence_files`，说明实际检查范围。

观察使用短片内从 0 开始的秒数，分别填写 `visible_fact`（画面事实）、`interpretation`（初步解释）和 `suggestion`（建议）。无证据时不填“好”或“需改进”。低清、遮挡、混合正反手、慢放和回放均写入限制；慢放文件的播放时间不能直接作为真实动作耗时。

`confidence` 使用高、中、低的文字级别，指助手对本项判断的把握，不是经过校准的模型概率。它不能消除不可见细节的限制。

## 导入与复看

初标 JSON 放在 `local/dataset/assistant-reviews/`，顶层包含 `clip_id`、`author: "assistant"`、`status: "draft"`、`requires_coach_review: true`、`summary`、`highest_priority`（可为空）、`review_method`、`coverage_notes`、`evidence_files` 和 `criteria`。六个 `criteria` 键与 [片段标签模板](../templates/clip-label.json) 相同；每项含 `judgment`、`confidence`、`reason_unobservable` 和 `observations`。每条观察含 `start_seconds`、`end_seconds`、`visible_fact`、`interpretation`、`suggestion`。

```powershell
.\.venv\Scripts\python.exe scripts/import_assistant_reviews.py --input local/dataset/assistant-reviews
.\.venv\Scripts\python.exe scripts/create_review.py
```

导入时校验片段 ID、六项字段、证据文件、时间边界和视频哈希，记录所看版本、规则版本及导入日期。不改变教练标签；初标更新时保留上一版。所有真实视频、初标与证据仍在被 Git 忽略的 `local/`。

之后教练可以只指出有分歧的编号、时间和原因。整理确认意见时填写 `review`，保留原始初标，以便知道哪些规则理解正确、哪些需要修改；不能因教练暂时没有反馈就视为认可。
