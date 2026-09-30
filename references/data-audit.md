# 数据证据与核验规范

## v2 结构

完整可运行的虚构输入见 [示例配置](../examples/config.json) 和 [示例证据](../examples/evidence.json)。采用 UTF-8 JSON。

配置包含 `schema_version=2`、`company`、`period.start/end`、`execution.mode=background_only`、`execution.allow_foreground=false`、`source`、`delivery` 和 `required_metrics`。飞书 `delivery` 明确 `profile`、`identity=user` 与 `folder_token`，不得跨身份替代。

证据顶层包含：

- `schema_version=2`、企业全称、实际起止日期及带时区的 `captured_at`。
- `capture.method` 只能是 `authorized_background_connector` 或 `platform_export`；`foreground_used=false`，`identity_verified=true`，并保留能证明商家的 `identity_evidence`。
- `metrics` 每条有 `metric`、数值 `value`、含原单位的 `raw_value`、`period_start/end`、`scope`、`source_module`、`source_label`、`source_ref`。来源引用须能定位证据，不能只是“后台数据”。
- `live_accounts={complete,rows}`；每行包含字符串 `id`、`name`、日期、范围、时长秒数、成交金额、券数与场次。
- `video_groups={complete,mutually_exclusive,rows}`；每行包含 `name`、`identity_type`（merchant / influencer / staff / ugc / other）、日期、范围、`new_video_count`、`video_play_count`、`video_direct_gmv`。身份类型根据平台分类映射，不能从账号昵称猜测；达人类型行的数量之和还要与 `influencer_video_count` 独立核对。
- `influencer_in_total` 标明达人视频是否包含在视频总量内。本版完整复盘的自动核验与发布只接受全量视频包含达人，即 true。若原始证据是 false 或未知，保留原义，不通过完整复盘门禁；先取得全量同口径数据，或由 Codex 只撰写明确限定范围的本地草稿，不走完整复盘的 analyze/publish。不能为了通过检查把它改为 true。

直播、视频分组行的 `scope` 与对应汇总范围一致，分组类别通过 `name` 区分。不同业务模块允许不同范围，但不能跨范围直接比较。账号 ID 用字符串保留精度。

## 核心字段及口径

| 类别 | 字段 | 含义 |
| --- | --- | --- |
| 经营 | gmv / redemption / refund | 平台成交、核销、退款金额，分别保留原标签 |
| 直播 | live_session_count / live_duration_seconds | 场次 / 时长秒数 |
| 直播 | live_viewers / live_gmv / live_coupon_count | 观看人数 / 成交金额 / 成交券数 |
| 视频 | video_count_total / influencer_video_count | 统计口径内视频数 / 达人视频数 |
| 视频 | video_play_count / video_direct_gmv | 视频播放量 / 视频直接成交 |

新增发布视频数不等于在播历史视频总数；必须从平台标签或定义确认后映射，不能因列名相近就当作同一指标。示例使用新增视频口径，平台不是该口径时须保留原义并调整分析。

完整复盘还应采集商品、门店、新老客等当前商家可用的经营证据。扩展指标同样提供日期、范围、来源。缺少模块时写明具体缺项，不以核心字段校验通过替代完整性判断。

## 关键核验

1. 每个模块单独确认实际日期。自然月按钮被选中不代表其他模块也已同步。日期文字变化但数值仍为旧请求时不得采纳。
2. 全部核心指标与配置区间一致，重复、缺失、负数、非有限数、布尔伪数值均不能作为有效证据。
3. 账号直播金额、券数、场次求和核对汇总。金额差异容差为 0.01 元；整数指标精确核对。直播时长细小差异仍提示复核，不自动当作准确。
4. 观看人数可能跨账号去重，不把账号人数相加当作总观看人数。
5. 只有分组完整且互斥，才合计视频数量、播放和直接成交；不得遗漏 UGC 后宣称全量闭合。
6. 达人属于总量子集时，不再次相加。达人数量、达人发布视频数量、视频发布账号数分别核对。
7. 期间播放量可能来自历史存量视频，不能直接除以本期新增视频数得出“平均单条播放”。平均值只在同一视频集合、同一时间窗下计算。
8. 核销与退款可能来自跨期订单，不直接用当期成交减退款命名“净收入”，也不将核销/成交比当作同批订单核销率。
9. 同一订单的内容场景、分销渠道等不同分类不能相加作为总成交。

## 对比

可选 `comparison={period,comparison_mode,metrics}`，其中模式为 `full_month` 或 `same_length`。完整自然月对完整自然月，仅紧邻上月才称月环比；未完结月及自定义区间优先同比较长度区间，明确实际日期。字段、来源、范围和单位一致后再比较。分母为零时不生成百分比增长率。

没有比较数据不阻塞独立区间报告，但必须说明无法判断环比。不能从营销计划、历史对话或平台模糊的涨跌标识反推基期。

## 输出与限制

`check` 返回 pass / warning / fail；存在 warning 或 fail 时禁止自动发布，先补齐证据或核实差异。不要为了通过检查删减核心要求。平台确实无某业务时，保留对应的零值及来源证据；无法获取与业务为零不是一回事。

脚本只能核查输入一致性，无法证明人工填入的来源真实。采集者仍须验证原始页面或导出文件，并把事实与解释分开。旧版 `review_month` 输入请迁移到明确日期区间，不再依赖隐含月份。
