# douyin-business-review

一个用于抖音生活服务“生意经”自然月经营复盘的 Codex Skill。它从已登录的企业数据页采集同口径数据，核对直播、短视频、达人、交易和门店指标，并生成紧凑、可追溯的飞书复盘文档。

## 核心能力

- 强制使用完整自然月，避免混入“近 7 天”“近 30 天”数据。
- 核对直播汇总与账号明细，识别时长、场次、观看和成交口径错位。
- 区分视频总量、达人视频量和发布账号数，避免错误相加。
- 校验短视频数量与播放量是否属于同一批内容和同一周期。
- 将事实、判断、动作和待确认项分开，避免把沟通记录写进正式复盘。
- 可选交付到飞书云文档，并要求写入后回读验证。

## 安装

```bash
git clone https://github.com/bigjun1-art/douyin-business-review.git ~/.codex/skills/douyin-business-review
```

也可以下载仓库后，将整个目录复制到 `~/.codex/skills/douyin-business-review`。

## 使用

在 Codex 中调用：

```text
Use $douyin-business-review to audit the currently open Douyin Business Analytics account for a natural-month review.
```

中文示例：

```text
用 $douyin-business-review 复盘当前 Chrome 中已打开的生意经企业账号，统计 2026 年 8 月自然月，并将报告写入指定飞书文件夹。
```

## 数据校验器

将采集结果整理为 JSON 后运行：

```bash
python3 scripts/validate_review_data.py review-data.json
```

只有 `status=pass` 的数值才能直接写成确定事实；`warning` 需要注明口径，`fail` 必须返回数据页复核。JSON 字段与交叉校验规则见 [references/data-audit.md](references/data-audit.md)。

## 运行要求

- Codex Skills 环境。
- 如需读取已登录页面，需要可控制用户 Chrome 的浏览器能力。
- 如需交付飞书，需要可用的飞书文档与云盘能力。
- Python 3.9 或更高版本，用于运行数据校验器。

## 隐私与安全

本项目不包含任何企业数据、账号数据或登录凭据。Skill 明确禁止读取或输出 Cookie、Token、密码、本地存储和鉴权头；遇到登录、验证码或安全校验时必须由用户本人完成。

## License

[MIT](LICENSE)
