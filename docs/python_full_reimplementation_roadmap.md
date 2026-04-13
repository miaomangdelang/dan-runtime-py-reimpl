# Python 版完整复刻路线图

更新时间：2026-04-13

## 目标

把 `pyimpl/` 从“二进制校准后的高完成度复刻”继续推进到“可持续维护、可重复验证、可逐步替换原二进制”的完整 Python 实现。

这里的“完整复刻”不等于盲目逐行照抄，而是要求下面 4 件事同时成立：

1. **行为对齐**：关键注册、OTP、OAuth、token 落盘、上传、web 管理面行为与二进制一致。
2. **结构清晰**：核心状态机、重试逻辑、配置入口、日志事件、测试夹具都能长期维护。
3. **验证可重复**：离线 smoke test、受控 live smoke run、故障归因和回归验证有固定流程。
4. **发布可用**：公开仓库可读、私有运行配置可注入、默认不会泄露 runtime 敏感信息。

---

## 当前状态（基线）

### 已完成

- `register_flow.py` 已按二进制校准主要注册主线：
  - homepage
  - csrf
  - signin
  - authorize
  - register.submit_password
  - sendOTP
  - validateOTP
  - createAccount
  - callback/session
- OAuth 主线已补齐到可验证形态：
  - authorize continue
  - password verify
  - conditional email OTP
  - about-you finalize
  - workspace / organization select
  - oauth token exchange
- Sentinel 已从单一 env token 占位实现推进到：
  - `env -> browser helper` fallback
  - flow-specific env key 支持
- Mailbox 已支持：
  - HTTP polling
  - message snapshot / recipient filtering
  - IMAP fallback
- 持久化已对齐：
  - `ak.txt`
  - `rk.txt`
  - `codex_tokens/*.json`
  - `registered_accounts.txt`
- 已有自动化验证：
  - `test_binary_flow_smoke.py`
  - `test_sentinel_solver.py`

### 还没完全闭环的点

- 真实网络端到端在特定代理链路下仍可能被首页 `403` / challenge 拦截。
- Sentinel 仍然优先依赖外部浏览器环境或外部 token 注入，尚未纯 Python 复刻 turnstile / proof-of-work 内核。
- `dan-web` 的 UI / 管理面虽然能跑，但与原二进制前端还没做到逐像素 / 逐交互完全对齐。
- live 运行的故障分类、观测字段、事件日志格式还有继续精炼空间。

---

## 路线图总览

## Phase 1：把主流程从“能跑”抬到“稳跑”

### 目标

让 CLI 单账号 live run 在受控环境里具备稳定的失败归因和更好的恢复能力，而不是只会“撞墙然后报错”。

### 任务

- 把 `visit_homepage -> get_csrf -> signin -> authorize` 的错误分类继续细化：
  - TLS / 证书问题
  - 首页 403
  - redirect 环异常
  - provider/challenge 页面未进入预期状态
- 为 `run_register()` 增加统一的 stage-level telemetry：
  - stage 名
  - HTTP status
  - final URL
  - replay 次数
  - whole-flow restart 次数
- 把当前 `WholeFlowRestartError` / `RetryableOAuthError` / `IncompleteRegistrationError` 的使用点再统一一轮。
- 给 live run 增加最小可读的 summary 输出。

### 完成标准

- live run 失败时，日志能明确告诉人：卡在 homepage / csrf / signin / oauth 哪一段。
- 重试策略不再散落在多个局部函数里乱飞。

---

## Phase 2：把 Sentinel 从“可用 fallback”推进到“可维护子系统”

### 目标

把 Sentinel 相关逻辑从业务主线里拆得更干净，降低后续继续复刻 `dan/internal/sentinel` 时的耦合成本。

### 任务

- 新增独立的 Sentinel provider 抽象层，例如：
  - env provider
  - browser helper provider
  - direct challenge provider
- 把 `kind -> flow -> page_url` 的映射集中定义并测试。
- 为 browser helper 补充：
  - stdout / stderr capture 归档
  - timeout / fallback browser path metrics
  - helper script 内容校验测试
- 研究是否把原二进制 `fetchSentinelChallenge` 中 `required / seed / difficulty / proofofwork` 的逻辑单独沉到 `pyimpl/danapp/sentinel_pow.py`。

### 完成标准

- Sentinel 不再只是几个函数拼一起，而是一个明确可替换、可扩展的组件。
- 后续即使要做纯 Python turnstile / PoW 复刻，也不用重写主流程。

---

## Phase 3：补齐 live 网络验证与代理矩阵

### 目标

建立受控的 live smoke run 矩阵，分清楚“代码问题”和“代理 / 风控 /页面变更问题”。

### 任务

- 定义 live smoke run 场景：
  - direct / no proxy
  - local proxy
  - remote proxy
  - insecure TLS debug mode
- 把每次 live run 的关键证据统一落盘：
  - final URL
  - response status
  - mailbox create result
  - OTP wait timeline
  - OAuth stage timeline
- 新增 `docs/live_validation_checklist.md`（待建）：
  - 跑前检查
  - 跑后确认
  - 常见失败模式
- 对典型失败分类建表：
  - homepage 403
  - TLS hostname mismatch
  - authorize continue invalid_auth_step
  - password verify invalid_state
  - sentinel browser helper timeout

### 完成标准

- 同一个问题能稳定复现并归类，不再每次都是“玄学挂了”。
- 代理链路问题和代码回归问题能快速区分。

---

## Phase 4：完善 `dan-web` Python 控制面

### 目标

把 `dan-web` 从“能触发批处理”的控制面，推进到“适合长期运维和观察”的控制面。

### 任务

- 补更多二进制同款状态字段：
  - stage 级状态
  - CPA retry 细项
  - register trace 汇总
  - last error / last final URL
- 丰富批处理显示：
  - whole-flow restart 次数
  - oauth retry 次数
  - sentinel fallback 来源
- 补 API 行为测试：
  - `/api/bootstrap`
  - `/api/status`
  - `/api/manual-register`
  - `/api/fill`
  - `/api/reconcile`
- 若需要，再补静态前端细节到更接近原二进制 UI。

### 完成标准

- `dan-web` 页面能让操作者看懂一批任务到底死在哪、重试过几次、有没有 token 留本地。

---

## Phase 5：补齐 token 生命周期管理

### 目标

把注册成功后的 token 流程做到闭环，而不是“注册完就算完”。

### 任务

- 把 `token_refresh.py` 的字段对齐继续做细：
  - `id_token`
  - `token_type`
  - session token fallback
- 为 token JSON schema 写固定断言测试。
- 审视 CPA 上传协议：
  - multipart 主路径
  - raw JSON fallback
  - 失败保留本地文件
- 如果需要，补 pending token retry 的专项测试。

### 完成标准

- 注册、落盘、上传、刷新这一套能连续验证。

---

## Phase 6：把“完整复刻”做成可发布工程

### 目标

让这个 Python 版不只是实验代码，而是一个可以发布、复现、交接的工程。

### 任务

- 补公共文档：
  - install / run / test / live validation / secret injection
- 给配置加 public-safe 示例文件：
  - `config.example.json`
  - `config/web_config.example.json`
- 如果需要，引入更正式的 packaging / task runner：
  - `Makefile` / `justfile`
  - `requirements.txt` / `pyproject.toml`
- 把敏感信息约束、公开仓库脱敏规则写进文档。

### 完成标准

- 新人拿到 repo，知道怎么装、怎么测、怎么注入私有配置、怎么跑 smoke test。

---

## 建议的优先顺序

如果按性价比排，我建议顺序是：

1. **Phase 1**：错误分类 + stage telemetry
2. **Phase 3**：live 验证矩阵 / 故障归因
3. **Phase 2**：Sentinel 子系统化
4. **Phase 5**：token 生命周期管理补齐
5. **Phase 4**：`dan-web` 控制面增强
6. **Phase 6**：工程化发布整理

原因很简单：
- 现在最大的不确定性已经不是“代码会不会写”，而是 **真实环境出问题时到底是哪里在作妖**。
- 先把观测和归因做好，后面每一轮 live 验证都值钱；反过来先狂堆功能，很容易把自己埋了。

---

## 完整复刻的验收定义

满足下面这些，才算 Python 版“完整复刻达标”：

- [ ] 单账号注册主线稳定跑通
- [ ] OAuth 主线稳定跑通
- [ ] Sentinel 至少具备 env + browser helper 两条稳定路径
- [ ] 关键错误有明确 stage 归因
- [ ] token 落盘格式、结果输出格式、上传行为与二进制一致
- [ ] `dan-web` 可触发并观察批处理
- [ ] 自动化 smoke test + live validation checklist 齐备
- [ ] 公共仓库配置已脱敏，私有运行方式有文档说明

---

## 当前结论

Python 版已经不是“原型”，而是进入了：

> **核心功能已基本复刻，剩余工作集中在稳定性、观测性、Sentinel 内核深化和工程化交付。**

也就是说，后面不是从 0 到 1 了，主要是从 **1 到 1.0**，把这套东西打磨成真的能长期用的工程。
