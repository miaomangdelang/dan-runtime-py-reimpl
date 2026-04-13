# Python 补全思路（基于 `dan-web` / `reimpl`）

## 我依据的线索
- `dan-web` 符号：
  - `dan/internal/danweb.(*Server).handleBootstrap`
  - `handleLogin` / `handleLogout` / `handleStatus`
  - `handleConfig` / `handleReconcile` / `handleManualRegister`
  - `dan/internal/danweb.(*Manager).StartScheduler`
  - `StartPendingTokenRetryLoop`
  - `ManualRegister` / `TriggerReconcile` / `fillToTarget`
- `dan-linux-amd64` / `dan-web` 共享符号：
  - `dan/internal/danapp.(*App).registerOne`
  - `newRegisterSession`
  - `visitHomepage` / `authorize` / `createAccount`
  - `sendOTP` / `validateOTP` / `callbackAndGetSession`
  - `saveCodexTokens` / `uploadTokenJSON`

## Python 侧映射
- `pyimpl/danapp/app.py`
  - 保留 `App` 作为核心协调器
  - 增加 `RegistrationRunner` 注入点，避免把真实网络流程硬编码进 CLI / Web
  - 增加 `MockRegistrationRunner`，先把“批处理、落盘、状态更新、Web 面板”打通
- `pyimpl/danapp/web.py`
  - `Manager` 对齐 `dan/internal/danweb.(*Manager)`
  - `Server` 对齐 `dan/internal/danweb.(*Server)`
  - 提供 `/api/bootstrap` `/api/login` `/api/logout` `/api/status`
  - 提供 `/api/config` `/api/manual-register` `/api/reconcile` `/api/fill`
- `pyimpl/danapp/register_flow.py`
  - 新增 `RegisterSession`
  - 新增 `OpenAIRegistrationRunner`
  - 串起 mailbox / signup / otp / callback / oauth / token save-upload
- `pyimpl/danapp/http.py`
  - 先补一个标准库 HTTP wrapper，后续真实实现可直接复用

## 建议的后续补全顺序
1. **继续用动态流量校准 live runner**
   - 校准 mailbox API 的真实 create/list/detail 路由
   - 校准 signup / oauth 的真实 body 字段
2. **把 retry / replay 逻辑补到和原二进制更接近**
   - whole-flow restart
   - 403 replay
   - oauth-not-required 分支
3. **把 token upload / refresh 的协议细节继续对齐**
   - CPA multipart/raw JSON 兼容
   - refresh endpoint 精确化

## 为什么先做 `dan-web`
因为从 `dan-web` 符号看，它已经把“调度 / 统计 / 触发批处理 / 配置修改 / 状态展示”这些控制面抽象好了。
先把 Python 控制面做实，后面只需要往 `RegistrationRunner` 里填真实注册流，不需要重写 UI 和批处理框架。
