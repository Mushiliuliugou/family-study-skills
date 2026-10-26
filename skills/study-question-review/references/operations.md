# 重温预览与操作协议 v1

从本Skill目录执行。数据库和地址由实际项目登记确定，不内嵌某个家庭的路径。Python 3.9以上，无第三方依赖。

## 只读盘点

```sh
python3 scripts/review.py plan --database /path/workbench/data/study.sqlite3 --date 2026-10-26 --out /path/reports
```

不提供limits时只盘点，不形成发布任务。脚本启用query_only，SQLite可能使用协调文件，但不会修改题目、日期或业务状态。

家长已指定题量时，例如明确要数学1题、英语1题：

```sh
python3 scripts/review.py plan --database /path/workbench/data/study.sqlite3 --date 2026-10-26 --limits '{"math":1,"english":1}' --out /path/reports
```

示例题量不是本Skill的默认家庭安排。零表示该科不分配，不能填负数、小数、布尔值或未启用科目。候选不够时整份预览不可发布，不悄悄少发或补入待核题。同日同科已有任务时，先检查现有任务，不能重复创建不同题单。

每题归入一类：ready可选、future未到期、paused家长处理/待核对、quality_blocked题面待核、reserved任务占用或同日已分配、archived归档/回收站、inactive未启用、data_issues数据缺口。合计必须等于题库总数，防止扫描遗漏。当前有缺口时先处理或由家长明确处理方案，再生成发布预览。

预览JSON含data_epoch、task_date、rules_snapshot、每题题目版本和复习版本、科目题量、稳定request_id及publish_payload，另保存plan_sha256防止文件无意改动。confirmed_priority读取家长保存的等级，默认U；review_policy_version核对执行规则版本，不读取外部分类讨论稿来代替正式确认。

## 通过现有服务发布

用户已批准这份预览时：

```sh
python3 scripts/review.py publish --plan /path/reports/plan-xxxxxxxx.json --base-url http://127.0.0.1:8766 --out /path/receipts --commit
```

仅支持localhost或127.0.0.1的HTTP根地址，不使用代理，不跟随重定向。默认验证目标为测试库。正式库仅在已有明确授权时加--allow-formal，不把确认两个Skill的分工等同批准某份任务题量。

发布使用工作台现有task-publish，保留服务的日期科目唯一性、题面状态、版本、事务和request_id幂等校验。规则变化、恢复资料、换错数据库或原题/复习状态变化会要求重新预览。原样重试已有request_id可得到原任务，不再次分配或登记学习结果。

CLI不会启动、替换或停止工作台服务。需要服务时使用原工作台启动入口；不要并行启动两个服务写同一库。日期为指定计划日；默认日期按Asia/Shanghai。

## 结果与日期

家长纸质批改后在原任务“批量反馈”保存。真实反馈遵循当前系统规则：独立通过进入真题库退出错题队列，提示后通过递进3/7/14/30天，失败默认次日且保留提示进度，待核对暂离普通到期队列；具体天数读取规则快照，不在本脚本硬编码新P阈值。

学生跳过或未提交不是错题判定；同题不同日期再次重做保留为新尝试，不新增重复题。旧题面快照与当前题面版本分开，避免后来修改导致反馈错位。

改期、取消未开始任务、整批撤销走原入口。若之后已有变更，按原入口版本保护处理；不要手写SQL强行恢复。大范围规则更新须先形成新版规则预览，是否追改已排日期由家长另行决定。

## 与入库Skill的交接

共用的关键标识：question_id、subject_id、source_intake_ids、数据版本、处理批次。入库回执说的是“资料整理写入”；重温发布回执说的是“任务已分配”；家长反馈事件才说明“本次作答结果”。三种回执不可互相替代。

新收题即使有智能体整理，题面仍可能needs_check。它应出现在quality_blocked，等待家长核对；不能为了进入今天任务自动清除待核。入库完成后重新运行盘点，就能看见是否具备重温资格。

## 二十分钟辅助推荐

`plan --recommend --target-minutes 15 --minutes '{"math":8,"english":4}'`按家长提供的每科每题平均分钟生成候选；默认5分钟只是未校准的占位值。目标允许0—20，0表示不安排。各科同一天已发布任务均计入预算，取消后释放；已批改也不重置当天额度。先查看题目再由家长调整估时；不能降低估时来硬塞更多题。默认只盘点，推荐也不自动发布。网页填写每科全部题总分钟，不能误作单题分钟。

发布检查当日各科合计预计不超过20分钟；实际时长由普通钟表提醒，到时即停，可提前结束。未做项保留跳过或待核，不记未通过，不自动降级或补作业。学生结束按钮只结束当前该科；其他科可以不开始，由家长集中反馈。全动作事件流水仍待接入。

批量反馈每题传reviewed_on（已核实实际重做日），可不同于批改日；默认任务日必须由家长核实。独立结果的间隔按reviewed_on计算，review_log.created_at记录批改/入账时间。待核对不建立完成事实。规则全文见[已确认规则](confirmed-rules.md)。

## 阅读理解专用摘要

阅读类使用scripts/reading.py输出JSON/Markdown，仅分析与分类，不输入原题。详见[阅读读取协议](reading-analysis.md)。正常任务发布改用/api/review-context精简上下文，不通过完整bootstrap取题面；过旧服务需先重启新版。
