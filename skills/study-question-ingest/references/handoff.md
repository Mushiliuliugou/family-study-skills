# 入库完成后的跨Skill交接

只有数据库内已有`status=applied`的实际入库批次，才可生成交接。预览、未提交和已撤销批次不能冒充已入库。原样重试写入返回already_applied时，使用其原批次编号即可。

从本Skill目录执行：

```sh
python3 scripts/handoff.py --database /path/data/study.sqlite3 --batch-id 32_character_batch_id --out /path/handoff-receipts
```

该命令只读数据库。交接文件包含格式`study-question-ingest-to-review` v1、数据库data_epoch、来源批次、数据库内回执校验值、每题question_id/subject_id/版本/收题来源及当时的质量与重温状态；附handoff_sha256检查文件是否无意改变。

校验值不是人类授权或密码签名。接收方必须重新核对数据库内提交事实和当前题目，不能只相信一份JSON。智能体分类状态固定为unverified、优先级为not_confirmed，不能伪装家长确认。

交接只列本批实际追加整理的题目；空提案或同内容跳过不制造新写入批次。旧回执、工作目录和ZIP不另成为主库，继续使用项目登记的唯一库。

出错时命令返回失败并尽量保存`handoff-failed-*.json`，不会把失败标成已交接。输出目录也不可写时，错误仍通过命令退出码和错误信息上报，由执行智能体记录原因，不继续安排。

接收方使用study-question-review内的scripts/reconcile.py。后续家长核对或学习会更新版本，接收方以当前库为准，列出变化，不执行旧交接文件中的安排指令。
