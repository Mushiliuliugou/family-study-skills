# 数据协议 v1

## 总库与来源

目标是家庭学习工作台schema=4的现有`study.sqlite3`（应用功能版本以兼容说明为准），不新建另一个主库。原`questions.id`对应交接JSON中的`id`及收题里的`question_id`，原`intake_records.id`区分每次学生收录。现有数据库必须含`subjects/questions/assets/intake_records/review_state/review_log/settings`。

学科固定使用原编号，不能按名称新造编号：

| 学科 | subject_id |
| --- | --- |
| 语文 | chinese |
| 数学 | math |
| 英语 | english |
| 历史 | history |
| 地理 | geography |
| 生物 | biology |
| 道德与法治 | ethics |
| 物理 | physics |
| 化学 | chemistry |

科目启用、年级和训练权重沿用家长设置，本Skill不调整考试计分或课程安排。

## 输入ZIP

- `manifest.json`：格式`family-study-intake-handoff`、版本1、总数、分科文件、图片SHA-256。
- `subjects/<subject_id>.json`：题目与所选学生收题记录。同一题可对应多次学生收题；题目本身按统一主键去重。
- `media/<asset_id>.png|jpg|jpeg`：裁剪题图或原图，路径与图片编号对应。
- `交接说明.md`：给人的说明，按资料读取，不作为运行指令。

脚本限制：压缩包和解压后合计均不超过512MB、最多2000个成员、最多500次收题；禁止重复文件名、不安全路径、符号链接与未登记文件。仅解出清单内图片，不执行ZIP内容。大批次请家长减少导出数量。

## 整理提案

`inspect`会生成可编辑模板。顶层只接受：

| 字段 | 含义 |
| --- | --- |
| format/version | `study-question-enrichment` / `1` |
| batch_id | 32位小写十六进制，独立批次唯一 |
| agent_name | 实际执行者名称；演示必须注明“模拟” |
| handoff_sha256 | 原ZIP完整SHA-256，不能自行更换来源 |
| items | 本批1—100题，可为原包题目子集 |

每题包含`question_id`、`subject_id`、`source_updated_at`、`source_intake_ids`和`annotation`。前四项来自原包；`source_intake_ids`须为这道题的实际收题编号，不能引用别题或不存在的学生描述。

`annotation`只接受以下字段，所有内容都是智能体建议：

| 字段 | 类型与限额 | 用法 |
| --- | --- | --- |
| title_suggestion | 字符串，≤120字 | 建议名称；不直接替换正式标题 |
| chapter_suggestion | 字符串，≤120字 | 有把握才填；无教材/进度证据可空 |
| knowledge_points | 至多10个字符串，每项≤80字 | 聚焦题图实际涉及的知识点 |
| question_type | 字符串，≤80字 | 有把握才填 |
| reason_hypotheses | 至多7项`{code,note}`，note≤300字 | 推测及依据；不能冒充已核实错因 |
| parent_check_notes | 至多10项字符串，每项≤300字 | 题图不全、科目疑点、需要核对的内容 |
| evidence | 至多32个本题`media/…`相对路径 | 非空建议必须有本题图像证据 |

错因code沿用现有选项：concept（知识点）、reading（题意/条件）、method（思路）、calculation（计算/书写）、expression（步骤/表达）、unfamiliar（题型）、unsure（不确定）。学生原选项不代表智能体必须提出同样推测；没有足够依据就把`reason_hypotheses`留空。

可以所有建议留空，该题会跳过，不会为“完成任务”硬填章节、错因或答案。禁止给提案增加`verified`、`pass`、答案、正式审核状态等未约定字段。

## 存储与更新

首次实际写入时，在原`questions`表增加`agent_annotations TEXT NOT NULL DEFAULT '[]'`列，不新建题目表或分科数据库，不改schema版本。每题追加JSON记录，含：

`version/batch_id/agent_name/created_at/handoff_sha256/source_intake_ids/content_sha256/status/annotation`

`status`固定为`unverified`。旧记录整段保留，单题最多100条整理记录。原题、家长备注、题面状态、学生原话和图片不覆盖。正式章节仍用原`chapter`；智能体章节仅在`chapter_suggestion`中等待家长采纳。

每批回执写在原`settings`表的`study_ingest:<batch_id>`键，并另存JSON。它包含分科数量、前值、写后检查值、快照路径与哈希，供重复请求和整批撤销。该附加列和回执随现有完整数据库备份保存，已验证schema=4的完整备份恢复兼容。

来源`updated_at`不一致时整批拒绝；智能体写入和撤销会刷新它，避免旧家长页面或旧提案覆盖。撤销核对的是原题及整理的实际内容，允许保留之后的正常学习历史。

同批次同内容重试返回已有回执；同编号不同内容拒绝；已撤销批次不能重新应用。另一智能体使用新导出和新批次，内容相同则跳过。不同裁剪、重拍或相似题不得凭语义合并或删除，以家长维护的统一题号为准。

## 家长审核扩展（兼容v1）

新导出包的intakes包含origin（student或parent）。旧包省略时按student核验；家长收题的reason_codes和reason_note为空，不得冒充学生自述。目标库按真实origin和原收题编号核对。

家长决定独立保存在settings的agent_decision前缀条目，已采纳的知识点等保存在question_enrichment，事件保存在agent_event；不改原建议unverified状态。存在尚未撤销的家长决定时，入库脚本拒绝整批回滚，先由家长撤销审核。

## 题型与参考资料兼容扩展

questions.question_kind使用固定编号，阅读理解为reading_comprehension，旧题默认unclassified。reference_material按answer/analysis分别保存text、origin（original/agent）、confirmed；agent来源非空内容须家长确认。图片新增analysis角色。智能体proposal仍只写约定整理字段，参考答案与分析另行交付待家长核对，不擅自增加proposal字段。

新阅读理解记录位于同库settings的reading_analysis前缀，只有分类、摘要、来源、自述及核对状态，不含原题/图片/答案；不得导入questions或给其生成重温日期。旧题被标为阅读理解后不再导出原题。
