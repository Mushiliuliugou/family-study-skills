# Family Study Skills · 家庭学习双Skill

面向家庭学习工作台的两个独立Skill：`study-question-ingest`负责将粗加工错题材料恢复为标准练习题，`study-question-review`负责根据学生与家长反馈准备一周候选，再由家长逐日分配。两者使用同一份按科目区分的权威数据库。

当前候选版：**1.9.1-rc.1**。适配已验证的工作台v0.10、数据库schema=4。Python 3.9+，脚本仅使用标准库。

## 获取与使用

1. 下载固定版本的仓库源码，保留两个Skill完整目录：`skills/study-question-ingest`和`skills/study-question-review`。
2. 把需要的完整Skill目录交给智能体，先读其`SKILL.md`，再按当前模式读取引用说明。仅给出Skill名称不代表已经安装或加载。
3. 在家庭学习工作台中，项目`04Skills/`下也需使用同一版本的两个完整目录；只更新全局Skill目录不会更新这个项目副本。不要覆盖数据库、图片或教材目录。
4. 先核对自己的目标库登记和身份。没有本机数据库访问能力的云端智能体只交付文件，不声称已经入库。普通错题交接ZIP用于生成standard-package.json及必要干净附图，家长通过工作台“标准入库”审核确认。知识点与错题类型可在同页修正；独立分类注释仍可另交proposal.json。

普通命令入口：

```sh
python3 skills/study-question-ingest/scripts/ingest.py --help
python3 skills/study-question-review/scripts/review.py --help
python3 skills/study-question-review/scripts/reading.py --help
python3 tools/verify_release.py
```

## 已实现的规则

- 各科共用一份分科总库与题号；来源、学生自述、智能体待核建议和家长确认分开保存。
- 各科辅助练习每天合计最多20分钟，可少做或不做；无必做题量，不使用AI计时。
- 阅读理解只保存分类和智能体分析，不输入原题、原文、题图或答案，不派原题练习。
- 强化真题与已掌握旧题归档分开；核对来源、题面及已学范围后由家长明确选题。
- 学生点开提醒即记录提示后通过；纸质提交与家长反馈分开，P映射待定，不自动改P2。
- 不承诺全系统所有旧动作已经纳入统一事件流水；具体缺口按使用者本机登记核对。

## 更新与校验

`release.json`记录本版文件校验值及每个Skill的源码指纹。工作台提示词应引用固定提交或发布版本，不能把main的后续变化当作已验收版本。

验收前先更新本机完整Skill目录并核对指纹，再在5—10题独立样本上验证；不拿正式全库跑调试。验收后如配置自动更新，仍需核对版本和实际执行副本，具体自动更新机制不包含在本仓库。

本仓库包含分发完整性校验和五个命令入口检查，不附真实学生数据或完整工作台。完整业务回归需要兼容工作台和使用者自己的独立轻量样本；不能用入口检查代替业务验收。

## 许可与资料范围

Skill源码与本仓库说明采用MIT许可。仓库不包含题库、题图、教材、试卷、成绩、学生姓名、学习记录或用户凭据；MIT许可不适用于使用者另行提供的学习资料。

## 1.9工作流程更新

入库Skill将粗加工照片恢复为无答案题面、必要附图和分离参考资料，交标准包由家长确认；重温Skill结合学生与家长反馈准备一周候选，家长逐日分配。旧注释proposal仍是可选补充，不等于标准入库。旧发布版本保留；工作台应在远程文件核验后绑定本版固定提交，并核对本机源码指纹。


## 1.9.1-rc.1 图片处理融合

入库Skill 1.9.1-rc.1新增默认图片摆正、轻微纠偏、外围裁边、压缩与原图保留规则，以及可独立执行的scripts/photo_prepare.py和assets/photo_prepare.swift。重温Skill仍为1.9.0-rc.1，规则未改。工作台已由自身独立实现改为调用Skill内同一工具；Codex直连先由宿主执行图片工具，再读取同一Skill规则看图加工，不要求家长重复描述图片要求。

本机图片工具需要macOS Vision/CoreImage及swiftc（可随Xcode命令行工具提供），首次编译后缓存；Python只需标准库，无新增账号、模型API费用或付费OCR。其他平台无工具时列能力缺口，不声称已处理。输入原文件不覆盖，参考内容仍需家长确认，几何处理不自动删除其他题或手写内容。图片处理回执含来源与输出哈希；没有清晰必要附图时仍待补。

示例（在入库Skill目录运行）：

```sh
python3 scripts/photo_prepare.py --input /path/photo.jpg --output /path/photo-prepared.jpg --report /path/photo-prepared.json
```

公开内容只有规则与工具源码，不含工作台、缓存、二进制、家庭题图、题库或教材。校验指纹包含.md/.py/.yaml/.swift，覆盖实际原生图像算法。
