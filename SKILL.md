---
name: douyin-business-review
description: Audit an authenticated Douyin Business Analytics (抖音生活服务生意经) account for a natural-month operating review, reconcile live/video/creator/transaction metrics, and produce a concise Feishu review document. Use for monthly business reviews from an already-open logged-in profile; do not use for influencer selection or public-profile research.
---

# 抖音生意经自然月复盘

把已登录的抖音生活服务“生意经”经营数据整理成可核验、可执行的月度复盘。默认交付是飞书云文档；用户指定其他载体时按其要求。

## 先确定任务参数

确认或合理推断：目标企业、复盘自然月、对比自然月、账号范围、参考框架、目标文件夹。用户只说“本月/这个月”时，以页面自然月筛选器对应的完整日历月为准；当月尚未结束则明确写“截至YYYY-MM-DD”，不得伪称完整自然月。

用户已明确要求操作其 Chrome 登录页时，使用 `chrome:control-chrome`。不得读取或输出 Cookie、Token、本地存储、密码或鉴权头；不得绕过登录、验证码或平台权限。

## 数据采集顺序

1. 核对页面企业名称、账号主体和当前筛选条件。
2. 每个模块都重新确认日期为目标自然月，并记录页面显示的起止日期；不要假定切换标签后筛选条件仍保留。
3. 依次采集经营总览、直播分析、视频分析、商品/交易、门店或账号明细。只采集报告需要的字段，保留页面名称和可见口径。
4. 对比期必须切换到上一个完整自然月重新读取，不用平台“近7天/近30天”代替。
5. 保存最小充分证据：关键汇总区、账号列表、内容列表或导出结果。可结构化读取时优先结构化读取；不得通过猜测补齐不可见字段。

详细字段与交叉校验见 [references/data-audit.md](references/data-audit.md)。写作结构见 [references/report-framework.md](references/report-framework.md)。

## 强制数据门禁

在写报告前，把采集结果整理为 JSON，运行：

```bash
python3 scripts/validate_review_data.py review-data.json
```

只有 `status=pass` 才可把数值写成确定事实；`warning` 项必须在报告中标注口径或“待确认”；`fail` 项必须回到页面复核。不得为了通过校验修改原始数值。

以下规则不可跳过：

- 每个核心指标必须绑定 `period_start`、`period_end`、`scope` 和来源模块。
- 直播场次、直播时长、直播观看、成交金额、成交券数必须来自同一个自然月和同一账号范围。
- 直播总量与账号明细能求和时必须核对；允许平台四舍五入误差，不允许把近7天明细与自然月总览拼接。
- 视频总量、达人视频量、发布账号数是不同概念。先确认达人视频是否包含在视频总量内，再计算占比；禁止默认相加。
- 播放量必须对应同一批视频和同一统计期。平台只给总播放量时不得用平均值乘条数反推；分组播放量无法对齐时标“口径待确认”。
- 环比必须使用两个完整自然月的同名指标和同一口径；分母为零时不用百分比表达。
- 页面显示单位如万、小时、分、秒、元、券必须标准化，但报告保留原始口径说明。

## 分析与写作

先写事实，再写判断，最后写动作。事实层只使用通过门禁的数据；判断要指出驱动因素、异常点和证据；动作要能落实到账号、内容、直播、门店或商品。

报告保持紧凑：经营结论、核心数据、直播复盘、短视频复盘、商品/门店表现、问题诊断、下月动作、数据口径。时间线原则上不超过三个经营阶段，除非用户明确要求逐日排期。

不要把用户的提醒、纠错过程、对话内容或操作步骤直接写进运营复盘。不要用外部行业数据代替企业内部经营数据；联网资料只用于补充方法或解释，并明确来源。

## 飞书交付

使用 `lark-doc` 创建正文、`lark-drive` 查找目标文件夹。涉及用户身份的飞书命令必须先按 `lark-shared` 校验身份；只有 `identities.user.verified=true` 且 `tokenStatus=valid` 才继续。

搜索目标文件夹时优先精确标题匹配，取得真实 folder token 后，用 `docs +create --as user --parent-token <folder_token>` 创建。创建完成后回读文档，核对标题、统计周期、企业名称、核心数据表、直播和短视频关键数值及待确认项。工具返回成功不等于交付完成。

## 停止条件

- 页面企业主体与用户指定企业不一致：停止，报告发现的主体名称。
- 自然月筛选不可确认、模块切换后日期异常、关键汇总与明细严重不一致：先复核一次；仍不一致则保留证据并标“待确认”，不得硬写结论。
- 出现登录、验证码或安全校验：请用户亲自完成，不重复尝试。
- 飞书出现 `operation not permitted`、`20064` 或 `20073`：立即停止，不清理令牌、不重复授权。
