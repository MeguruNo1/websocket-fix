# DNS 与目标节点路由

适用于代理端口正常但 Codex 仍反复重连，尤其是 TUN 已开启、目标域名超时、系统 DNS 与可信 DoH 不一致，或用户要求地区优先和自动选节点。先检查当前状态；仅诊断请求不修改配置。

## 区分 DNS 和远端节点故障

- 对比系统 `getaddrinfo`、`scutil --dns`、代理内部 DNS、经已验证代理访问的可信 DoH。fake-ip 的 `198.18.x.x` 是正常映射；不同 CDN 地址本身不足以证明污染。
- 对同一目标，以可信 DoH 返回的地址做一次保留主机名/SNI 的对照（例如 curl 的 `--resolve`），配合超时和 DNS 差异定位。直达干净地址收到 403 只证明网络路径可达；不要将该 IP 写成长期规则，也不要把它当作 WebSocket 验收。
- 同一节点能打开 Google、却无法打开 `https://chatgpt.com/cdn-cgi/trace`，应继续检查目标节点/路由。DNS 修复后 WebSocket 恢复，但 HTTPS 仍超时，也可能存在第二个节点故障。
- 目标 trace 返回 200 可用于节点健康检查；最终仍使用当前 Codex 二进制的 doctor 和当前应用日志验收。Dot 还需实际 `connected` 与目标对话恢复。

## 找到持久源和实际运行配置

Clash Party/Mihomo 的订阅、覆写源和生成的运行配置是不同文件。

1. 从当前 Mihomo 进程参数读取 `-d` 和 `-ext-ctl-unix`。运行配置可能位于 `.../mihomo-party/work/config.yaml`；不能把根目录 `mihomo.yaml` 直接当作当前进程的运行配置。UNIX socket 也可能随进程变化，不沿用旧路径。
2. 找到当前启用的持久覆写。优先在该覆写中最小修改，再通过应用保存/重新生成。只改 `work/config.yaml` 可能被订阅更新覆盖。
3. 完整配置和覆写先备份到用户私有目录，目录 700、文件 600；不要输出节点密码、订阅 URL、完整配置或完整环境。诊断报告也限制权限，因为出口 IP 属于网络元数据。
4. 改动后运行当前 Mihomo 的 `-t -f <候选运行配置>`。加载可用本机官方控制 API：`PUT /configs?force=true`，JSON 为 `{"path":"绝对运行配置路径"}`，预期 HTTP 204；必要时 `POST /cache/dns/flush`。`GET /configs` 通常只返回基础设置，不能据此断言 DNS policy 已加载。
5. 对照持久源和生成配置，并验证一次应用重新生成后仍保留规则。重载前保存现有 Selector 的 `now`；重载后只恢复被改变且仍有效的原选项，保留用户其他选择。测试自动组时不要手动锁定其当前节点。

### Clash Party 覆写的易错点

此应用的 YAML merge 使用 `+` 前缀将数组插到原数组前面，`+` 后缀追加到后面。通配域名键本身也以 `+` 开头，因此**覆写源必须转义为 `<+.chatgpt.com>`**，生成的 Mihomo 配置才应是 `+.chatgpt.com`。直接在覆写源写 `+.chatgpt.com` 会被误解为合并指令。按当前应用版本验证，不把该语法套给其他客户端。

示例说明结构，代理组名需对应当前已验证配置；保留已有精确域名 policy。DoH 的 `#组名` 表示经该组连接，避免 DoH 又走故障节点。

```yaml
dns:
  nameserver-policy:
    '<+.chatgpt.com>':
      - 'https://1.1.1.1/dns-query#Codex OpenAI'
      - 'https://8.8.8.8/dns-query#Codex OpenAI'
    '<+.openai.com>':
      - 'https://1.1.1.1/dns-query#Codex OpenAI'
      - 'https://8.8.8.8/dns-query#Codex OpenAI'
'+rules':
  - DOMAIN-SUFFIX,chatgpt.com,Codex OpenAI
  - DOMAIN-SUFFIX,openai.com,Codex OpenAI
```

检查这些定向规则在生成配置中位于更宽规则之前。不要为解决两个服务的故障重写整个订阅、扩大为全机代理或自动修改系统 DNS。代理内部 policy 不能保证 macOS resolver 已改变；只有系统解析路径仍失败时，才转到 Dot 参考中的精确 `/etc/resolver` 条件方案。

## 按实际出口和目标延迟选择节点

- 地区优先来自用户当前偏好，不能将示例中的地区固定为所有用户的默认设置。
- 节点名称只用来找候选。通过该节点访问 Cloudflare trace，读 `ip` 与 `loc`；`colo` 是 Cloudflare 数据中心，不能当作节点出口国家。`loc` 是服务对出口 IP 的分类，不能证明机器的物理位置。宽泛名称也可能有目标地区出口。
- 优先地区节点需再次访问目标服务 trace，并核对出口国家。记录目标可达性和延迟；出口探测成功而目标服务失败，不能算健康节点。
- 用户要求保留暂时不可用节点时，将已确认属于目标地区的节点留在自动池中，由健康检查暂时排除、恢复后重新参加选择。没有出口证据的节点只能标为待确认，不能宣称已验证国家。
- 推荐“地区内 url-test + 地区间 fallback”：内层按目标 URL 延迟选节点，外层先使用首选地区池，仅在池全部不可用时使用已验证的其他地区。单个混合地区 url-test 无法保证地区优先。

覆写的 `+proxy-groups` 示例（尖括号节点名需换成已验证的实际名称）：

```yaml
'+proxy-groups':
  - name: Codex Japan
    type: url-test
    proxies: ['<已验证日本出口节点1>', '<已验证日本出口节点2>']
    url: https://chatgpt.com/cdn-cgi/trace
    expected-status: 200
    interval: 300
    timeout: 6000
    max-failed-times: 2
    lazy: false
    tolerance: 50
  - name: Codex OpenAI
    type: fallback
    proxies: ['Codex Japan', '<已验证其他地区节点>']
    url: https://chatgpt.com/cdn-cgi/trace
    expected-status: 200
    interval: 300
    timeout: 6000
    lazy: false
```

300 秒检查和 50 毫秒切换容差仅为配置示例，可按用户要求调整。容差减少小幅延迟波动带来的切换，因此不要承诺每时每刻都绝对选择最快节点。

### 可复用的隔离探测

使用 [probe_proxy_exits.py](../scripts/probe_proxy_exits.py) 读取当前运行配置，为指定节点建立独立 Mihomo 进程和仅监听 `127.0.0.1` 的临时 HTTP 端口。它不改变主实例的配置/选项，不安装依赖，结束后清理临时凭据和进程。需要 Python、PyYAML、curl 和当前 Mihomo 二进制；PyYAML 缺失时可在临时目录安装后通过 `PYTHONPATH` 使用。含 `dialer-proxy` 的节点会被拒绝，因为其出口依赖其他代理，不能直接展开为独立节点。

示例用于只读探测：按现场替换绝对路径和接口。TUN 开启时先确认真实出网接口再传 `--interface`，避免临时代理的出站再次进入主代理。以下 `--country JP` 只对日本出口进一步探测目标服务；未传 `--node` 时枚举配置中的全部内联节点，不读取 provider 外部文件。

```bash
python3 "$HOME/.codex/skills/websocket-fix/scripts/probe_proxy_exits.py" \
  --runtime-config '/实际路径/work/config.yaml' \
  --mihomo-bin '/实际路径/mihomo' \
  --interface en0 --country JP \
  --report '/用户私有目录/node-exits.json'
```

`--runtime-config` 是当前生成配置；`--mihomo-bin` 是当前核心；`--interface` 指定真实出网接口；`--country` 是两位国家代码；`--report` 写入权限 600 的 JSON。可重复 `--node '精确节点名'` 限定范围；`--parallel` 限制并发，`--timeout` 为每次请求设秒数上限。报告仅含节点名/类型、出口 IP/国家、耗时及目标状态；完整凭据不会进入报告。探测异常时先检查接口、配置兼容性和 DNS，不自动改主配置或安装全局依赖。

## 验收

保存配置后，在没有额外代理环境变量和临时 feature 开关的新 doctor 进程中检查主要 HTTP 服务与 WebSocket 101；另查当前桌面 PID 在修复后是否仍产生重试。短时恢复、新进程加载配置、当前进程恢复和重启后稳定性分别报告，不合并成“永久解决”。

系统 DNS 异常和目标节点故障可能同时存在；分别验证定向 DoH policy、目标规则和候选节点。节点名称不能代替出口国家证据，出口正常也不能代替目标服务可达性。保留暂时不健康节点时，由健康检查控制其是否参与选择。所有耗时、节点状态和重启结论均以本次实际测试为准。

官方参考：[Clash Party YAML 覆写](https://clashparty.org/docs/guide/override/yaml)、[Mihomo 控制 API](https://wiki.metacubex.one/en/api/)、[Mihomo url-test](https://wiki.metacubex.one/en/config/proxy-groups/url-test/)。
