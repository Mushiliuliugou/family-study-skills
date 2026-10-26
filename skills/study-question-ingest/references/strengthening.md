# 强化真题入库模式

适用于新增用于薄弱知识点加强练习的考试题，不是对已掌握旧题转库，也不是阅读理解原题导入。权威位置按数据库登记；题目bank_scope=strengthening，元数据在同库settings的strengthening:<题号>，不新建主库。

## 准备与校验

先查家长已采纳的知识点与实际反馈，再选择对应已学范围的原题。公开检索先给来源链接；没有完整题面不能根据网页残缺公式补造真题。标明中考exam、校内考试school_exam、模拟simulation、不明unknown。只有前两类且家长已确认来源、题面、已学范围，才可练习。来源确认须有paper、year、region、locator；网址不是自动认证。智能体不能代替家长勾选三个confirmed字段。

阅读理解使用reading_analysis入口，不能带原题入强化库。题面与答案分离；reference_material的answer/analysis分别使用text、origin（original/agent）、confirmed；智能体答复必须家长确认。没有答案可暂存待补，禁止自拟答案冒充原卷答案。

## 本机接口

页面“强化真题库”可直接操作并下载模板。智能体仅向登记工作台的本机服务提交：GET /api/strengthening；POST /api/media先保存PNG/JPG，得到asset_id；POST /api/strengthening-preview校验；家长已授权这份具体内容入库时POST /api/strengthening-import。角色、Origin、本机客户端和data_epoch保护沿用现有接口，不直接SQL写库、不向外部上传家庭资料。

JSON顶层为request_id（32位小写十六进制）和records（1—10条）。每条：

- id（统一题号，同样32位）；subject_id、title、question_kind（禁reading_comprehension）。
- knowledge_points和error_types各1—10项，每项80字内；linked_question_ids可选，只关联同科普通错题，最多20题。
- source_kind、paper、year（四位年份或空）、region、locator、source_url（HTTP/HTTPS或空）、acquired_via。
- source_confirmed、content_confirmed、curriculum_confirmed，均为家长真实确认的布尔值；未确认false。
- images，每项original_id、asset_id、role（prompt/figure/answer/analysis）；至少一张prompt，最多16张。图片先经本机上传，不接受任意本地文件路径或远程抓图。
- reference_material，使用上述答案/分析规范。
- 修改已有记录还需expected_updated_at；不传回ready、review、created_at等只读字段。

预览不写题目。导入按请求号幂等；同请求内容变化拒绝，同科相同题图指纹提示编辑已有题，不重复建题。整批失败不写题目和成功回执。修改保留前后事件，未反馈任务占用的题不允许修改。若来源有误，可编辑并取消来源/题面确认，使其退回备选；不删除历史或复制另一份掩盖错误。

导出是study-strengthening-export查阅格式，含题图编号并依赖原工作台媒体，不是可随意跨库导入的独立ZIP。交付导入回执、真实题号和待核项；不替家长分配任务、不改原错题P值或学习结果。
