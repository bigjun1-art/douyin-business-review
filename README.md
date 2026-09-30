# douyin-business-review

抖音生活服务生意经经营复盘 Skill，v2.0.0。支持自然月或指定日期区间，参数化核验、计算和飞书交付，全程禁止抢占桌面及浏览器控制权。

## 能做什么

- 参数包含企业、起止日期、浏览器资料名称、飞书用户配置和目标文件夹。
- Python 脚本核对每项指标的周期、范围和来源，交叉校验直播账号明细、短视频数量及播放量。
- Codex 根据核验后的事实撰写完整经营复盘；脚本不伪造经营原因或代替业务判断。
- 飞书发布先校验身份，再创建、回读正文、验证目录；保留状态，防止异常后重复创建。
- 没有安全的后台采集通道时明确阻塞，不回退到鼠标、键盘、窗口切换或用户页面导航。

**能力边界：本仓库没有内置生意经私有接口、登录器或浏览器采集驱动。** 数据须由当前环境中已授权且明确支持不抢焦点的连接器提供，或来自平台导出。仅“Chrome 已登录”不代表后台采集可用。数据就绪后，本仓库可在命令行完成核验、计算和发布，无须控制桌面。

## 安装与调用

首次安装（目标目录尚不存在时）：

```bash
git clone https://github.com/bigjun1-art/douyin-business-review.git ~/.codex/skills/douyin-business-review
```

已有安装应先保留本地修改，再更新技能文件，不覆盖私人数据或配置。依赖 Python 3.10+（核心脚本仅使用标准库）；发布脚本面向 macOS / Linux，飞书交付另需已授权的 `lark-cli` 用户配置及云文档/云盘权限。Windows 发布未验证。

在 Codex 中调用：

```text
用 $douyin-business-review 给示例烘焙有限公司做 2025-08-01 至 2025-08-31 的经营复盘，
使用指定的浏览器资料定位商家，输出到个人飞书中转站。
全程后台执行，不切换或操控我的页面；缺少后台采集能力时说明具体缺项。
```

模型读取技能并编排已授权的步骤；这不是离线运行即可自行登录和采集的独立产品。

## 脚本快速验证

以下仅使用虚构数据，不连接浏览器或飞书：

```bash
python3 scripts/review_pipeline.py check --config examples/config.json --data examples/evidence.json
python3 scripts/review_pipeline.py analyze --config examples/config.json --data examples/evidence.json --out /tmp/review-example-analysis.json
python3 -m unittest discover -s tests -v
```

实际任务参数、发布命令与中断恢复见 [后台运行说明](references/background-runbook.md)。
证据字段见 [数据核验规范](references/data-audit.md)；正文要求见 [复盘框架](references/report-framework.md)。

## 安全与验证

禁止读取、导出或提交密码、Cookie、Token、鉴权头及浏览器用户目录。真实证据、任务配置、报告与交付状态放在仓库之外；公共示例均为虚构数据。自动测试使用模拟飞书响应，不代表真实平台的连接、权限或接口版本已验证。

[MIT License](LICENSE)
