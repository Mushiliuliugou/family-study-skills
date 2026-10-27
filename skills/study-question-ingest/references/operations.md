# 命令与回执

以下从本Skill目录运行。路径由实际环境给出，不内嵌特定用户目录。Python 3.9或以上，无第三方运行依赖。原ZIP和输出目录都放在数据库资料目录之外，以免数据库恢复切换时移动它们。

## 核验与提案

```sh
python3 scripts/ingest.py inspect --handoff /path/student-handoff.zip --out /path/work/batch-a
```

生成`proposal.json`、`source-index.json`及本包图像。用图像查看能力实际看图后修改模板。填写真实agent_name，保留batch_id和来源字段，补充annotation；多题包可先只保留本批要处理的items。空字段代表尚无可靠结果，不由脚本自动调用模型补齐。

```sh
python3 scripts/ingest.py plan --handoff /path/student-handoff.zip --proposal /path/work/batch-a/proposal.json --database /path/workbench/data/study.sqlite3 --out /path/work/batch-a/reports
```

只读预览：分科变更数、统一题号、建议内容、跳过原因、保留字段。不修改数据库，也不因没有`test_database`而拒绝只读预览。

## 追加待核信息

已有明确写入授权时，在指定测试库执行：

```sh
python3 scripts/ingest.py apply --handoff /path/student-handoff.zip --proposal /path/work/batch-a/proposal.json --database /path/test-data/study.sqlite3 --out /path/work/batch-a/reports --commit
```

测试库settings中的test_database必须为字符串`true`。用户明确批准正式接入并指定目标时，才可对正式库另加`--allow-formal`；参数只防误用，不证明授权。不要为了绕过保护把正式库标成测试库。

脚本先获得SQLite写锁，核验全批，再通过独立只读连接创建一致性快照，之后单事务追加。任何一题预检失败或中途写失败都不部分入库。仅追加智能体建议，故数据库快照覆盖此次改动；原图和教材不写入也不复制。正式首次接入前保留工作台完整ZIP备份。

输出状态：

- `preview`：只读预览；也可能是全部空建议/相同结果而无变更。
- `applied`：新增待核整理已提交；不等于家长核实或学习完成。
- `already_applied`：同批次重复请求，未再次追加。
- 错误：整批未写入；回报具体冲突并重新导出，不重造题号。

写入回执文件`apply-*.json`，安全快照`before-*.sqlite3`及其SHA-256。数据库内settings回执是提交事实依据；若提交后输出文件保存失败，原样重试查回已有回执。

## 按批撤销

```sh
python3 scripts/ingest.py rollback --database /path/test-data/study.sqlite3 --batch-id 32_character_batch_id --out /path/work/batch-a/rollback --commit
```

先创建撤销前数据库快照，再整批恢复该批之前的智能体整理。题目或整理内容在之后变化时整批拒绝，以免覆盖后续修改；不改学生提交、家长结果、复习阶段和复习历史。保留数据库中的撤销回执，不删除来源和审计。

撤销后不要直接重用旧批次；重新从工作台导出，再建立新批次。整体数据库快照用于事故恢复凭据，不作为覆盖用户后续学习记录的日常撤销方法。

## 给家长的回报

用中文说明本批有哪些科目、几道题新增整理、几道跳过、哪些内容待核；交付proposal、plan和执行后receipt。不要把测试回执当成正式入库凭证，不将模拟代理称为真实不同智能体测试。

家长界面已提供建议展示、修改后采纳、拒绝、暂缓及撤销，操作见[家长审核与交接](parent-review.md)。脚本只追加待核建议，家长逐字段采纳与学习结果分开记录；不自动安装Skill，不新增收费或注册资源。


## 标准化入库 v2工作流程

本节为1.9版主流程；此前inspect/plan/apply/rollback属于“待核分类注释”模式，不等同完整题面标准化。

工作台导出原交接ZIP时附standard-template.json。按模板逐题实际看图，完成后命名standard-package.json。模板题目需沿用question_id、subject_id、expected_updated_at及data_epoch；批次32位小写十六进制。每批1—30题，设计调试5—10题，不强行凑数。缺题面和必要图形的题列待补，不写入合格包。

顶层严格字段：format="family-study-standard"、version=1、batch_id、data_epoch、questions。
每题严格字段：question_id、subject_id、expected_updated_at、title（≤100字）、chapter（≤120字）、question_kind、practice_text（≤12000字）、practice_image_ids（最多16个已上传图片编号）、figure_required（布尔）、reference_material、gaps（字符串列表）。

practice_text是无答案题干及选项；practice_image_ids是干净题图或必要附图。两者至少一项非空；依赖图形时figure_required=true且必须有图片。不能把原作答照片直接冒充干净图。数学公式用可可靠显示的普通文本/Unicode；复杂公式可交干净图，别依赖未配置的LaTeX渲染器。
reference_material中answer、analysis各自为{text,origin,confirmed}；origin仅original或agent。智能体新拟内容必须confirmed=false，家长在界面确认后才能保存为参考。原图中的答案/分析继续保留，未补齐的参考资料列gaps；参考缺项不冒充已验证。

家长操作：标准入库页上传JSON→修正题面/附图编号/参考内容→取消不合格题→核验预览→勾选家长确认→标准入库。必要附图先在同页上传，返回asset_id再填入JSON/表单。不要先删除原照片。分类知识点错因建议仍可单独交proposal.json在分类建议审核处理。

本机API：POST /api/standard-preview {package:<标准包>}只读；POST /api/standard-import {package:<标准包>,confirmed:true}经家长授权后提交。沿用已有parent角色、Origin、data_epoch等保护。POST /api/media先上传干净图。能力不足时由家长在界面导入，不远程猜测本机地址，不另建主库。

正式库写入会校验全批来源版本、科目、题型、必要图形及任务占用，先保存数据库快照，再单事务保存标准题面、题目版本和事件。原照片保留，学习结果和P值不动；只解除质量待核，首次日期按原收题日计算。暂停/已掌握记录不自动重新练习。同批同内容可重试，不同内容必须新批次。学习图片完整备份仍用工作台备份入口。

标准题可附classification对象：knowledge_points与error_types两个字符串列表，各最多10项、每项100字。家长在标准入库页修正核对后，一并保存到同一题号的分类记录；不包含P值。旧proposal仍可用于单独追加分类，但同一交接的来源版本一旦已变，需要重新导出；不能强行同时套用旧版本。
