---
name: websocket-fix
description: Websocket修复：诊断并修复 macOS 与 Windows 上 Codex 的 WebSocket 超时、反复重连、代理未生效、DNS 与目标节点异常，Dot 云端对话恢复失败（app-server unavailable），以及 Computer Use 控制子进程代理缺失导致的疑似固定等待；验证实际连接并追查复发原因。不用于一般网站的 WebSocket 业务代码。
---

# Websocket修复

目标是使发生故障的 Codex WebSocket 实际经过用户当前代理，并通过对应端点的握手验证。用户只要求检查或解释时执行只读诊断；明确要求修复时直接完成必要配置和验证，无需重复确认。

## 平台分流

- **Windows**：先读 [Windows 诊断、持久修复与验收](references/windows.md)。以当前桌面后端实际 `codex.exe` 路径执行命令，确认 WinINET、WinHTTP、代理环境与实际连接；不要运行下面的 `scutil`、`lsof`、`launchctl` 或 `/etc/resolver` 命令。
- **macOS**：继续使用下文及原有参考中的 macOS 流程。历史 PID、路径、端口与测量数值均需现场重查。
- 两平台可用 [diagnose_network.py](scripts/diagnose_network.py) 在隔离子进程中运行 doctor、保存仅网络项的私有报告；支持已保存配置、临时系统代理开关、显式 HTTP 代理三种模式。脚本不改配置、不重启应用；只认实际 HTTP 101，并分别报告必需 HTTP 与可选资源状态。当前版本 doctor 的 `checks` 可能是映射或列表，不假定只有一种结构。

如果问题是 Computer Use 每次操作前固定等待二十多秒、网页文字或结构读取慢，先读 [Computer Use 代理排查与验收](references/computer-use-proxy.md)，按控制组件分支处理。模型 WebSocket 的系统代理开关不能替代控制子进程的代理检查。

如果问题是 Dot 的“恢复对话失败 / Codex app-server is not available”，或日志出现 `hostId=durable`，先读 [Dot 云端连接代理](references/dot-cloud-proxy.md)。模型连接的代理配置和 doctor 的 Responses 握手不能代替该分支验收。

如果本机代理端口可达，目标域名却超时，或 TUN 已开启仍失败，读 [DNS 与目标节点路由](references/dns-and-node-routing.md)。该分支覆盖持久覆写、实际出口国家探测和按用户地区偏好自动择优；节点名称和 Google 测速不能代替目标服务验证。

## 定位与诊断

- 查找当前运行的应用、Codex 后端 PID 和实际二进制路径，不硬编码旧 PID。应用可能安装为 ChatGPT.app 或 Codex.app；以当前进程实际路径为准。
- 用 `scutil --proxy` 获取代理启用状态、主机、端口和排除规则。Clash Party/Mihomo 常见回环代理地址如 `127.0.0.1:7890` 仅作示例；每次现场确认实际端口。
- 用 `lsof -nP -a -p <后端PID> -iTCP` 区分到代理的连接和外部直连。`SYN_SENT` 代表尚未建连。一个进程同时走代理和直连时，不能仅凭某条代理连接断言 WebSocket 已走代理。
- 对实际二进制运行 `doctor --json`，保存到任务的 `work/`。重点检查 `network.env`、`network.provider_reachability`、`network.websocket_reachability`。总退出码可能因 TERM=dumb 或其他无关检查非零；按检查项判断，不把总退出码等同网络失败。若版本不提供 doctor，先检查 help，再采用可用日志及握手诊断。
- 必要时只读查询 `$CODEX_HOME/logs_2.sqlite`（未设置 CODEX_HOME 时使用 `~/.codex`）；先确认 schema。只取相关时间段的 `codex_api::endpoint::responses_websocket`、`codex_core::responses_retry`、`falling back to HTTP` 等记录。裁剪日志上下文，不输出认证头、cookie、令牌或完整环境。
- 区分模型 `/backend-api/codex/responses` WebSocket 和 `/backend-api/wham/remote/control/server` 远程控制 WebSocket。模型握手成功不能证明远程控制也恢复。
- HTTP 可达不代表 WebSocket 可用；未认证 GET 的 405 仅证明到达 HTTP 服务。日志中的 `falling back to HTTP` 解释了重试后恢复回答，并不代表 WebSocket 修好了。

## 首选持久修复

先确认当前版本支持 `respect_system_proxy`：可查看 `features list`、doctor 输出或本机实现；不要猜测未知配置键。Windows 的配置位置、ACL 备份与命令示例见 Windows 参考；以下持久修复的测试与验收标准两平台相同。

1. 在不注入代理环境变量的条件下，用当前二进制执行 `--enable respect_system_proxy doctor --json`，确认 `network.env` 显示系统代理已启用、provider 的主要 HTTP 端点可达，并且 WebSocket 返回 `HTTP 101 Switching Protocols`。可选 CDN/资源检查单独记录，失败时不要宣称所有网络检查通过。若当前命令环境已有 HTTP_PROXY、HTTPS_PROXY、ALL_PROXY 及其小写形式，测试子进程中移除它们，保留其余环境；不得修改当前全局环境来做对照。
2. 测试通过且用户要求修复时，把当前 `config.toml` 备份到用户私有目录（macOS 目录权限 700、文件 600；Windows 使用当前用户 SID 的私有 NTFS ACL，不能以 chmod 代替），再运行 `features enable respect_system_proxy`。预期改动仅为 `[features]` 中的 `respect_system_proxy = true`。检查差异，保留所有其他配置。已启用时不重复修改。完整应用和代理配置可能含凭据，不放进工作仓库或诊断输出。
3. 不带临时开关、不注入代理变量，再运行 `doctor --json`，验证保存后的配置被读取、WebSocket 实际握手成功并记录耗时。
4. 当前版本若将该功能标为开发中，简短告知；不要为了隐藏提示而修改其他选项。
5. 区分“保存配置并在新进程验证成功”和“当前桌面进程已生效”。用当前 PID、重试日志和实际任务判断当前连接；网络路由修复可以使旧进程恢复，此时无需强制重启。配置开关是否被旧进程读取是另一件事。若确需重启，说明原因，用户重启后检查新 PID 和连接日志；不要在活动任务中擅自杀死承载当前对话的应用。未经重启测试，不宣称重启后的结果已经验证。

如果开关不存在或测试失败，不反复写入同一设置。可以在独立诊断子进程中显式设置 HTTP_PROXY、HTTPS_PROXY、ALL_PROXY 为已确认可用的代理地址，测试实际 WebSocket 握手。以该版本的支持情况选择后续办法，避免修改应用二进制、关闭 TLS 验证、擅自开启全机 TUN 或改写无关网络规则。不要把 `shell_environment_policy` 的子命令环境误当成运行中的模型客户端环境。

## 修复复发时

检查历史方案是否仍在，以及启动时是否被实际使用：

以下 LaunchAgent 与 `launchctl` 检查仅适用于 macOS；Windows 用对应参考检查用户环境、启动器、计划任务及新进程，不创建 macOS 启动项。

- 检查已有代理环境 LaunchAgent，例如 `~/Library/LaunchAgents/com.example.proxy-environment.plist`（示例名称，以实际文件为准），确认其是否通过 `launchctl setenv` 设置 HTTP_PROXY、HTTPS_PROXY、ALL_PROXY 和 NO_PROXY。读取现有文件和 `launchctl print gui/<uid>/<label>`，检查执行次数及退出码；RunAtLoad 的一次性任务执行完后显示 not running 是正常的。
- `launchctl getenv` 中存在变量，不代表已经运行的主应用和后端继承到了变量。分别检查主应用及后端的启动时间与代理变量，只显示代理相关键值，不输出完整 `ps eww` 内容。
- 启动项执行成功、登录会话变量存在，仍不能证明主应用或 Codex 后端已继承代理。分别比较显式代理环境与系统代理开关的实际握手结果，并确认当前进程状态。
- 启动顺序或启动方式可能造成继承失效；除非找到直接证据，必须标为推测。仅凭应用更新时间不能断言更新清除了配置。
- `respect_system_proxy` 方案减少对登录启动项环境继承的依赖，但不得声称永不复发。保留现有启动项，不因本技能执行就删除其他应用可能依赖的配置。

## 交付

简洁报告：直接原因及证据、实际改动、握手结果与耗时、当前桌面进程是否已生效、必要的重启步骤。没有成功握手时明确仍未修复，继续定位实际失败环节，不用 HTTP 成功代替验收。
