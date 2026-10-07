# Windows 诊断、持久修复与验收

用于 Windows 原生 Codex 桌面与 CLI。WSL 是独立运行环境；Windows 的注册表代理和配置位置不自动适用于 WSL。共用 SKILL.md 的握手验收、DNS/节点、Dot 和 Computer Use 分支，按本参考替换 macOS 工具。

## 1. 找到实际后端、配置与代理

PowerShell 中按实际进程发现，不把 PATH 中的 CLI 当作桌面后端：

```powershell
Get-CimInstance Win32_Process |
  Where-Object { $_.Name -match '^(codex|ChatGPT|mihomo)\.exe$' } |
  Select-Object Name,ProcessId,ParentProcessId,ExecutablePath
Get-Command codex.exe -All | Select-Object Source
```

`ExecutablePath` 可能因权限返回空值，这表示未知，不证明进程未运行。Codex 的 MSIX 主应用可能位于 WindowsApps，后端位于用户 LocalAppData 的带版本散列目录；不要硬编码散列。仅在需要定位代理运行配置时私下查看对应进程 CommandLine，不输出完整命令行。普通用户读取提权代理进程可能受限；可从当前客户端日志、安装注册表项和运行文件交叉定位，不为诊断擅自提权。

指定查到的完整路径，用调用运算符运行；路径含空格或 `!` 时避免交给 cmd.exe 拼接：

```powershell
$codexBin = '实际后端 codex.exe 的绝对路径'
& $codexBin --version
& $codexBin --help
& $codexBin features list
```

若旧 PATH CLI 对当前配置报未知 `service_tier` 等值，先核对版本并换用桌面实际后端。不要为运行旧诊断工具而改写用户模型、服务档位或清空配置。确认 `doctor` 与 `respect_system_proxy` 存在后再用它们。

配置目录：已设置的 `CODEX_HOME`，否则 `$env:USERPROFILE\.codex`；SQLite 使用实际后端报告的路径。只显示相关 feature 值，不输出完整 config.toml 或 auth.json。

同时读取两套代理：

```powershell
Get-ItemProperty 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Internet Settings' |
  Select-Object ProxyEnable,ProxyServer,ProxyOverride,AutoConfigURL
netsh winhttp show proxy
```

WinINET 注册表代理、WinHTTP 服务代理与进程环境相互独立。WinHTTP 显示 Direct 不代表桌面不使用系统代理。ProxyEnable=1 也不证明当前 WebSocket 使用该代理。ProxyServer 可能是单个 host:port，也可能包含 `http=...;https=...;socks=...`；PAC 的结果不能从静态端口推断。只使用现场确认可用的协议与端口，排除规则需保留本机控制通信。

对后端实际 PID 查看连接：

```powershell
Get-NetTCPConnection -OwningProcess <实际后端PID> -ErrorAction SilentlyContinue |
  Select-Object LocalAddress,LocalPort,RemoteAddress,RemotePort,State,OwningProcess
```

State 数字 5 表示 Established、3 表示 SynSent；CIM/序列化输出可能显示数字。本地代理连接必须与请求时间、后端日志和 Mihomo 连接元数据关联，不能仅凭一条 127.0.0.1 连接宣称模型或 Dot 已恢复。普通 CIM 不能可靠读取另一个 Windows 进程的环境；只读当前 shell 的环境不能证明桌面已继承。不要安装侵入式工具、转储进程内存或输出完整环境来绕过此限制。

## 2. 隔离对照与最小持久修复

使用 Python 3 的标准库脚本；report 写在仓库外的任务 work/ 或用户私有目录。脚本使用当前用户 SID + icacls 设置私有文件 ACL，不改父目录权限。它运行子进程时移除大小写代理变量，保留其他环境；Windows 环境键不区分大小写，因此只注入一套。

执行外部 skill 校验器时，若其默认文本编码仍为 GBK，用 `python -X utf8 <校验器> <skill目录>` 读取中文 UTF-8 文件；这不需要修改系统语言或现有 skill 内容。

```powershell
$skillRoot = '当前 websocket-fix skill 目录'
python "$skillRoot\scripts\diagnose_network.py" --codex-bin $codexBin `
  --mode clean --report '.\work\baseline-report.json'
python "$skillRoot\scripts\diagnose_network.py" --codex-bin $codexBin `
  --mode system --report '.\work\system-report.json' --rounds 2
python "$skillRoot\scripts\diagnose_network.py" --codex-bin $codexBin `
  --mode explicit --proxy 'http://127.0.0.1:实际端口' `
  --report '.\work\explicit-report.json'
```

`clean` 使用已保存配置且不注入代理环境，不能等同于强制直连：已启用的 feature、系统回退或 TUN 都可能代理请求。`system` 临时启用 feature，`explicit` 只在子进程注入 HTTP 代理。脚本不支持配置写入；通过报告逐项核对实际模式、HTTP 101 与耗时。可选 CDN 失败单独报告，必需 HTTP 失败继续排查；总 doctor 退出码不能代替网络分项判断。脚本自身退出码 0 表示所有轮次 101 且必需 HTTP 通过，1 表示未达到验收或执行失败。

测试系统代理握手与必需 HTTP 均通过后：

1. 将 config.toml 备份到新建用户私有目录；macOS 的 700/600 不适用于 NTFS。可导入 `scripts/platform_support.py` 的 `protect_private_path`，仅保护新建备份目录；先保护目录再复制配置。或获取 `whoami /user /fo csv /nh` 的 SID，再 `icacls <新目录> /inheritance:r /grant:r '*<SID>:(OI)(CI)(F)'`。核对返回码和最终 ACL；不修改现有共享目录、原配置 ACL 或整个用户目录。
2. 用实际后端运行 `features enable respect_system_proxy`，已有 true 时跳过。对比 TOML 语义及文件差异，确认只有这个开关改变。其他配置保持原值。
3. 用 `--mode clean` 再运行两轮，确认保存配置生效、101 与必需 HTTP 通过。当前功能若仍标为 under development，简短说明。
4. 查当前桌面日志和实际任务，不擅自结束当前应用。若旧后端没有读取新 feature，说明需要用户重启后验收；不能把新 doctor 的成功当作旧进程已生效。

若系统模式反复 EOF/超时，而显式模式成功，先定位系统代理选择与节点差异，不盲目写入 feature。确需显式代理启动时，准备可审阅的启动器：从已验证安装方式解析当前应用路径，只对子进程设置代理变量，保留 NO_PROXY；已运行 Electron 单实例可能仍转交旧实例，因此需正常退出后启动并确认新 PID。不默认改用户/机器全局环境、WinHTTP、登录启动项或 binary，也不用 shell_environment_policy 冒充模型代理配置。

## 3. DNS、节点与 Windows 控制接口

结合 [DNS 与目标节点路由](dns-and-node-routing.md)；替换 `scutil --dns` 为 `Get-DnsClientServerAddress`、`Resolve-DnsName` 和 Python `socket.getaddrinfo`。系统 hosts 位于 `$env:SystemRoot\System32\drivers\etc\hosts`；只看失败域名。fake-ip 不是污染证据。curl 使用 `curl.exe`，避免 Windows PowerShell 的 curl 别名；PowerShell 5.1 可能丢失空字符串参数，`--noproxy ''` 等对照优先通过 Python subprocess 参数列表运行。不关闭 TLS 校验来获得成功结果。

Clash Party 数据可能在 AppData/Roaming/mihomo-party，仍需现场确认。先定位持久覆写、profile.yaml/override.yaml 与生成的 work/config.yaml。进程可能经 `-ext-ctl-pipe` 暴露 Windows 命名管道，不能套用 macOS UNIX socket 或凭空启用 TCP 控制端口。管道名称可随重启改变；只枚举相关命名管道并核对当前进程。控制 secret 在内存中读取，不输出到命令行、报告或仓库。命名管道的并发连接可能返回 busy/Invalid argument，串行访问并设置调用的外部时间上限，不无限重试。

`GET /proxies/<节点>/delay?url=<目标URL>&timeout=...` 可定位目标失败的节点；不会切换 Selector，但健康数据可能更新。延迟成功还需 trace/doctor 验证，节点名称不能当作实际出口国家。优先用隔离探测避免切换全局节点；`probe_proxy_exits.py` 现在支持 Windows，隐藏临时核心窗口，临时凭据/报告使用 NTFS ACL，退出时清理核心和临时文件。按需 `--interface` 使用当前真实网卡名称，不能照搬 en0；不开启 TUN、不安装全局依赖。

修复只覆盖已确认失败的目标规则与 DNS policy；生成配置先经当前 Mihomo `-t` 验证，再通过现有受支持 API/客户端加载，保留其他 Selector 的选择。若现有自动组只测 Google，不能代表 ChatGPT 健康；可建立仅针对 Codex 目标的独立组，候选经过目标测试，并保留用户原有通用选择。没有地区偏好就不固定日本优先。只改生成配置不是持久修复，需验证覆写重新生成。

部分 Clash Party 版本缓存覆写索引：直接写入 override.yaml 的新条目可能直到客户端重载/重启才被采用。优先对已启用的持久覆写做最小合并，或通过当前支持的客户端接口注册；保留已有 DNS 与其他键。只读检查当前安装实现可确认缓存与文件读取行为，并在隔离进程中用实际合并函数验证重生成的配置与已验收配置一致，不执行或修改整个应用包。核心 API 重载与桌面客户端重新生成是不同动作，尚未进行的动作分别报告。

不要在 Windows 创建 /etc/resolver、LaunchAgent，或把 macOS supplemental resolver 方案改写成全机 DNS。NRPT/hosts 属系统级变更，应在证据明确且用户授权相应范围后另行处理；通常先修代理内部定向规则。

## 4. 当前进程、Dot 与 Computer Use 的验收

doctor 的 desktop.app.version 通常提供当前桌面日志目录；Windows 常见 `$env:LOCALAPPDATA\Codex\Logs`，不能只查 Roaming/Codex。按日期、主进程 PID 和修改时间选择当前日志。SQLite 以 mode=ro 打开，先 PRAGMA table_info，按时间、thread_id、target 查询与裁剪 message/feedback_log_body，隐藏认证信息。不要把完整 DB、对话日志或 doctor 全报告提交 Git。

- 模型：`responses_websocket` 握手/连接成功，当前任务继续完成，修复后时间段没有同一 EOF 重试；观察窗口和重启状态分别交代。
- Dot：按 [Dot 参考](dot-cloud-proxy.md) 确认 durable 的实际 endpoint、connected/initialized 与目标对话恢复。模型 101 不证明 Dot，未认证 401/403 只证明传输可达。
- Computer Use：按 [控制子进程参考](computer-use-proxy.md) 查看当前 Windows 控制服务与 node 进程（名称可能与 macOS 不同），确认 MCP 专用 env 支持，再用同一浏览器和公开页面的文字/结构读取复测。不能以 model feature、curl 或 js_reset 代替控制服务新 PID 和同页验收。

只报告实际得到的结果：配置已保存、新进程验证、当前桌面恢复、重启后验证是四个不同状态。失败轮次不能被成功轮次掩盖；未握手仍未修复。
