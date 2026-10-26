# 强化真题按需练习模式

强化库是同一权威数据库中的bank_scope=strengthening，不是已掌握旧题归档。普通scripts/review.py盘点和自动候选排除强化题，不把强化题反馈产生的日期变成额外日常作业。

## 选题依据

GET /api/strengthening提供已存题、可用状态、家长已采纳知识点和未撤销的实际提示/未通过反馈。weakness_groups按科目和知识点汇总关联题号和反馈记录数；这不是全部错题率或自动评级证据。没有确认知识点就列待补，不能从题名或学生自述猜测高频原因。需要收题时转交入库Skill，不索取阅读理解原题。

## 预览与发布

默认只给候选和依据。家长明确所选题、日期和规划用时后，通过页面或现有POST /api/task-preview：顶层bank为strengthening、task_date、subjects。每科subject_id、count、question_ids、estimated_minutes（该科本次全部题的总分钟）。不传question_ids即拒绝，不自动抽题；只能用家长已确认来源、题面和已学范围的真题，不能模拟题冒充真题，不能阅读理解。

家长授权这份具体预览后，POST /api/task-publish保持bank=strengthening，附request_id和每题versions（预览中的updated_at）。接口沿用parent角色、Origin、本机客户端和data_epoch保护。普通命令行publish用于原普通重温计划，不伪造plan文件绕过选题资格。

当天各科所有任务共用20分钟上限，通常15分钟，可为零；强化不是额外20分钟。同日同科已有任务阻止重复发布。未反馈任务占用题目，不能重复分配或修改题面。预览不是发布，不直接改review_state。

## 反馈与对账

学生仍只见当天题面与题序，纸上作答、提交/跳过，提交不判掌握。家长按原任务批量反馈，区分独立通过/提示后通过/未通过/待核；原有日期与撤销规则继续使用。反馈保存在同一题号和任务历史，相关状态可在强化库查阅，题目仍留在强化库，不混进普通已掌握旧题入口或普通到期队列。再次加强由家长明确选择。

只读reconcile识别strengthening_bank来源分组，仍核对任务/反馈/题图关联；它不把强化元数据缺少普通错题PDF定位误报为漏入库。全动作统一事件流水仍有原登记的缺口，不作零遗漏承诺。
