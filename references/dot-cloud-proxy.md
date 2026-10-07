# Dot 云端连接代理

适用于 Dot 的“恢复对话失败 / Codex app-server is not available”，尤其是 `hostId=durable`、`remote_unavailable`、`open_timeout` 和关闭代码 `1006`。不要将这个通用报错直接认定为本地 app-server 崩溃。

## 先确定连接对象

- 当前进程日志中分别检查 `hostId=local` 的初始化和 `hostId=durable` 的连接。客户端可能本地正常、云端持续失败。
- 从当前版本实现或日志确认云端地址；某历史版本的 durable 连接地址为 `wss://codex-cloud-backend.chatgpt.com/`。不要套用旧版远程控制 `/backend-api/wham/remote/control/server` 地址。
- 检查系统代理、主应用/后端各自继承的代理变量及进程连接。Chromium 网络服务或本地模型后台经过代理，不能证明主进程中的云端 WebSocket 也经过代理。
- `doctor` 的 `network.websocket_reachability` 测试 Responses 端点。其返回 101 只能证明模型连接正常。

## 检查当前客户端的代理实现

只读检查安装包时截取相关函数，避免输出整段打包源码或任何认证信息。需要确认：

1. durable 端点构造与实际 URL。
2. 传给 WebSocket 的 `agent` / `createConnection` 选项。
3. 代理选择函数是否覆盖公网 durable 域名，还是只覆盖内部域名。
4. 底层库是否自设 `createConnection`，导致绕过 Node 默认全局 Agent。

某历史版本的实现：公网 durable 分支未配置显式 Agent；SOCKS Agent 仅在其他分支或内部域名启用。底层 ws 自设 TLS `createConnection`。因此，即使 HTTP_PROXY/HTTPS_PROXY 已被应用继承，也不能断言云端连接会使用代理。不要照抄该版本压缩函数名；更新后重新检查。

## 用隔离探针区分路径与会话

对已确认的云端主机做有超时上限的对照：直连、显式 HTTP CONNECT 代理，以及必要时与应用相同的自建 TLS 连接语义。不要修改全局环境进行测试。

- Node 新版支持 `NODE_USE_ENV_PROXY=1`，但自设 `createConnection` 且未传显式 Agent 的请求可能绕过默认 Agent。不能仅凭运行时支持此变量就将它写入 launchctl、启动项或技能修复步骤。
- 当前 Node 若支持，可在独立探针传入 `https.Agent({proxyEnv: {HTTPS_PROXY: confirmedProxy, NO_PROXY: ''}})`，证明显式 Agent 的路径是否可达；这不会改变桌面应用的连接。
- 对同一云端主机比较自建 TLS 连接与显式代理 Agent，分别记录超时或 HTTP 响应。HTTP 403/401 只证明收到 HTTP 响应，不代表认证 WebSocket 握手或会话恢复成功。
- 不因未认证请求返回 403 就诊断账号被封或会话无权限。验收需要应用实际云端 101/connected，随后恢复目标对话成功，并观察不再重试。

## 修复边界与交付

优先使用当前版本实际支持的该连接代理设置。若不存在，明确说明需要客户端层面的支持，不要伪造配置键，也不要用模型 `respect_system_proxy` 或 Chromium `--proxy-server` 冒充 Node ws 的修复。

修改应用资源前检查签名与资源完整性机制。上述版本 Info.plist 含 `ElectronAsarIntegrity`，并存在 Electron fuse 设置；直接修改 app.asar 不是普通配置操作。不要通过破坏或关闭完整性校验来强行实施代理修复。不要在当前聊天中退出承载任务的应用。

只有已验证配置入口和影响范围后才实施修复。若唯一后续路径需要扩大到系统透明代理/TUN，先说明具体影响并取得用户对此范围的授权；仅授权某服务器代理不等于授权全机路由改动。

没有受支持的客户端入口时，报告“诊断完成，尚未修复”，而不是把检查结果写成已生效方案。技能可先保存已验证的诊断和限制，待实际修复成功后再补入操作步骤。

参考：[Node HTTP 内置代理](https://nodejs.org/api/http.html#built-in-proxy-support)。官方文档说明默认全局 Agent 的环境代理功能，不保证应用自定义连接使用它。

## TUN 已开启但 TLS 仍失败：核对系统 DNS

共享的 DNS 对照、Clash Party 覆写键转义、实际运行配置识别和节点择优步骤见 [DNS 与目标节点路由](dns-and-node-routing.md)。精确 durable 域名规则不能自动覆盖 `chatgpt.com` 的模型端点；按当前失败主机选择范围。下述 `/etc/resolver` 是系统解析路径仍失败时的条件方案，不是每次修复的必做步骤。

若 TUN 已启用仍在 TLS 建立前断开，应对照系统 resolver 与经代理的可信 DoH。可使用可信 DoH 地址做保留主机名/SNI 的短时对照；不能继续将所有失败归因于 WebSocket 代理缺失。

- 对比系统 `getaddrinfo`、`scutil --dns`、经代理的可信 DoH，以及 Clash DNS 返回结果。TUN fake-ip 返回 198.18.x.x 是正常映射，不能当作污染。
- 用当前 Clash 版本的持久覆写功能，为精确域名设置 `dns.nameserver-policy`，DoH 经已验证存在的代理组连接；不硬编码云端实际 IP，不改整个订阅的 DNS。
- 可建立独立 YAML 覆写，使该域名经已验证代理组访问可信 DoH；例如使用 `https://1.1.1.1/dns-query#<已验证代理组>` 和 `https://8.8.8.8/dns-query#<已验证代理组>`。本机 DNS 可仅监听回环地址，端口需现场确认。
- 若系统仍查询 LAN 路由器，Clash 内部 policy 本身不能改变 macOS resolver。可准备 `/etc/resolver/codex-cloud-backend.chatgpt.com`，内容为 `nameserver 127.0.0.1`、`port 1053`、`timeout 3`，只匹配此域名。写入需要管理员授权；已有不相同文件应停止覆盖并先检查。仅在 TUN 和本机监听已验证可用时采用；关闭 Clash 后此域名解析也会受影响。
- 完成系统规则后，检查 durable WebSocket 的 connected/initialized 状态及目标对话恢复事件；分别报告当前会话、重启测试与长期观察结果，尚未测试的项目不得宣称通过。
- 完成系统规则后以 `scutil --dns` 和系统 `getaddrinfo` 验证；`dig` 默认查询不一定使用 macOS supplemental resolver，不能单凭默认 dig 否定规则生效。最终仍须检查 durable 连接初始化与目标对话恢复。

## 验收与后续维护

按实际故障组合验证 TUN、Clash 精确域名 DoH policy、本机 DNS 监听与 macOS 单域名 supplemental resolver。确认域名查询经已验证的代理路径，并以实际云端连接和对话恢复为准；不能概括为单独开启 TUN 就一定足够。

单域名 resolver 依赖 Clash 的 127.0.0.1:1053 监听；停用本方案时需同时移除该精确 resolver 文件并停用对应覆写，避免遗留无响应的 DNS。移除涉及管理员权限且要确认内容属于本方案，不要清理整个 /etc/resolver。后续应用若原生支持代理，先验证新路径再撤除此方案。
