# 后台脚本运行说明

## 环境及输入

Python 3.10+；核验与分析只使用标准库、离线运行。发布调用现有 `lark-cli`，不安装浏览器驱动、不处理登录凭据。先读 [后台采集约束](background-capture.md)，确认当前环境存在可用的非抢焦点连接器或平台导出。

实际文件放在仓库外的私人任务目录。以下参数均为示例：

```bash
python3 scripts/review_pipeline.py init --company "示例烘焙有限公司" --start 2025-08-01 --end 2025-08-31 --browser-profile "示例商家资料" --profile personal-example --folder-token example-folder-not-live --out /tmp/example-review-config.json
python3 scripts/review_pipeline.py check --config /tmp/example-review-config.json --data /path/to/private/evidence.json
python3 scripts/review_pipeline.py analyze --config /tmp/example-review-config.json --data /path/to/private/evidence.json --out /path/to/private/analysis.json
```

`init` 不覆盖已存在的配置。运行前把示例替换为已核实的任务参数；资料名称用于定位，不证明当前页面属于该商家。证据规范见 [data-audit.md](data-audit.md)。

## 撰写及发布

Codex 使用核验结果、原始依据和用户框架撰写完整正文。脚本并不自动生成有商业判断的最终文章。飞书 XML 使用当前可用的飞书文档技能或官方 CLI 格式说明编制；不要用其他工具提前创建同一文档，发布脚本是唯一创建入口。

```bash
python3 scripts/review_pipeline.py publish --config /path/to/private/config.json --data /path/to/private/evidence.json --report /path/to/private/report.xml --state /path/to/private/delivery-state.json
```

发布顺序：证据校验 → XML/周期/标题核验 → 同一 profile 用户身份有效性检查 → 飞书格式预检 → 创建到目标目录 → 立即记录文档 ID → 回读正文 → 分页核对父目录。所有凭据调用串行，明确 `--as user --profile`；权限或鉴权异常即停，不重新登录、不切换机器人、不扩大权限。

格式检查和接口兼容性以当前已安装 CLI 为准。自动测试仅模拟外部接口；若真实 CLI 响应结构不兼容，安全停止而不是猜测成功。

## 恢复与防重复

状态文件和配置/证据/正文指纹绑定。相同任务已完成时只复核已有文档；内容改变时拒绝借用旧状态，不能以删除状态绕过检查并自动重建。创建返回警告时保留已创建的 ID 并保持未验证，重跑不会消除警告；先独立核实资源或格式问题，本版不自动覆盖修复。

创建请求已发出但未取得可确认的文档 ID 时，结果属于未知，不能再次创建。先只读检索目标目录，找到唯一匹配文档后通过以下方式绑定回读：

```bash
python3 scripts/review_pipeline.py publish --config /path/to/private/config.json --data /path/to/private/evidence.json --report /path/to/private/report.xml --state /path/to/private/delivery-state.json --document-id VERIFIED_DOCUMENT_ID
```

多个可能文档无法唯一识别时停止并说明。恢复参数只绑定已存在文档，不创建、不覆盖。并发锁存在时先确认原进程状态，不自动删除锁强行启动。

## 测试与证据

```bash
python3 -m unittest discover -s tests -v
python3 scripts/review_pipeline.py check --config examples/config.json --data examples/evidence.json
```

交付说明区分：脚本测试通过、真实采集是否可用、飞书是否真实回读成功。脚本比较的是全部可见文字及父目录，不证明图片、画板、超链接目标或视觉样式完全保真；报告包含这些资源时，另用已授权的后台能力核实，无法核实时不得声称其已验证。后台不是承诺任务脱离宿主后持续执行；长任务须由宿主支持的后台运行机制托管，不能用抢占浏览器来替代缺失能力。
