# Keycloak 统一身份认证接入设计

## 1. 背景与目标


本次接入公司 Keycloak 统一认证平台，目标是：


本设计不包含管理驾驶舱、部门级数据授权、细粒度业务资源授权、历史数据迁移和测试用户创建。

## 2. 已确认的 OIDC 信息

| 项目 | 值 |
| --- | --- |
| Keycloak 地址 | `http://10.8.6.32:18080` |
| Realm | `company-dev` |
| Issuer | `http://10.8.6.32:18080/realms/company-dev` |
| Discovery URL | `http://10.8.6.32:18080/realms/company-dev/.well-known/openid-configuration` |
| 前端 Client ID | `rixin-contract-comparison-web` |
| 前端 Client 类型 | Public |
| 前端流程 | Authorization Code + PKCE S256 |
| Redirect URI | `http://127.0.0.1:5173/callback` |
| Post Logout Redirect URI | `http://127.0.0.1:5173/` |
| Web Origin | `http://127.0.0.1:5173` |
| 后端 Audience | `rixin-contract-comparison-api` |
| 签名算法 | RS256 |
| 角色 Claim | `resource_access.rixin-contract-comparison-api.roles` |

后端 Client Secret 不参与 Bearer JWT 的本地签名验证，本期不写入代码、前端配置、示例环境变量或本文档。原返回单中的 Secret 已经通过非安全文本渠道出现，上线前必须由认证管理员轮换。

## 3. 技术路线

采用标准 OIDC SPA + FastAPI Resource Server：

- 前端使用 `react-oidc-context` 和 `oidc-client-ts`。
- 前端以 issuer 作为 authority，通过 discovery 自动发现授权、Token 和退出端点。
- 后端通过 discovery 获取 `jwks_uri`，不硬编码 JWKS、授权或 Token 端点。
- 前端解析 Claim 仅用于展示和入口显隐；后端验证后的用户上下文是权限判断的唯一可信来源。
- Token 通过 `Authorization: Bearer` 传递，不使用 URL 查询参数传递 Token。

未采用 Keycloak JavaScript Adapter，是因为标准 OIDC 客户端更直接符合 discovery-first 要求，也降低对 Keycloak 专有前端 API 的绑定。未采用 BFF，是因为当前仅提供 Public SPA Client，BFF 还需要 confidential client、服务端会话和 CSRF 防护，超出本期范围。

## 4. 前端认证设计

### 4.1 登录与回调

应用启动后先恢复 OIDC 会话。在没有有效会话时，立即跳转 Keycloak，不显示本地用户名密码表单。跳转前将当前应用路径、查询参数和 hash 保存到 OIDC state。

Keycloak 完成认证后回到固定地址 `http://127.0.0.1:5173/callback`。前端处理授权响应，恢复原目标地址；如果没有合法目标地址，则回到首页。恢复地址只能是当前应用内部路径，禁止接受外部绝对 URL，避免开放重定向。

认证层必须区分以下状态：

- 初始化和会话恢复中：显示全屏加载状态，不渲染业务页面。
- 回调处理中：显示认证处理中状态。
- 已认证：渲染业务应用。
- 认证失败：显示错误信息和手动重试入口，不能无限自动跳转。

### 4.2 会话存储与退出

OIDC 会话使用 `sessionStorage`，不再使用旧的 `rixin_contract_auth_user`，也不把 Access Token 或 Refresh Token 写入 `localStorage`。Access Token 临近过期时通过 OIDC 客户端刷新；刷新失败后清理会话并重新认证，自动重试次数必须有上限。

退出时调用 OIDC end-session 端点，完成 Keycloak 单点退出，并回到 `http://127.0.0.1:5173/`。退出完成后清理本地 OIDC 状态。

### 4.3 用户信息

前端用户模型包含：

- `sub`：唯一用户 ID。
- `preferred_username`：用户名。
- `name`：展示名。
- `email`：邮箱。
- `department_code`：部门编码。
- `department_name`：部门名称。
- `roles`：从固定应用角色路径读取的角色集合。

`sub` 是唯一身份依据。用户名、姓名和邮箱仅用于展示或日志。缺少可选展示字段时使用用户名或 `sub` 的安全降级值，不影响认证结果。

### 4.4 受保护请求

所有业务请求必须通过统一认证请求层：

- JSON 和表单 API 自动添加 Bearer Token。
- 报告下载先通过认证请求获取 Blob，再触发浏览器下载。
- PDF.js 使用 `httpHeaders` 添加 Authorization Header。
- 原生 `EventSource` 无法设置 Authorization Header，因此进度流改为基于 `fetch` 和 ReadableStream 的 SSE 客户端。
- `401` 清理无效会话并重新认证。
- `403` 显示无权限页面。
- `404` 显示“任务不存在或无权访问”。
- `503` 显示统一认证服务暂时不可用，并提供手动重试。

角色判断在前端只用于入口和按钮显隐，不能替代后端权限检查。

## 5. 后端认证设计

### 5.1 模块边界

认证能力拆分为以下聚焦组件：

- `AuthSettings`：读取并校验认证配置。
- `OidcDiscoveryProvider`：加载和缓存 discovery 文档及 JWKS。
- `JwtValidator`：验证 Access Token。
- `CurrentUser`：承载经过验证的用户 Claim 和角色。
- `get_current_user`：FastAPI 认证依赖。
- `require_roles`：接口角色依赖工厂。
- `TaskAccessPolicy`：执行任务归属和管理员全量访问判断。

API 路由只声明依赖、调用应用服务并映射 HTTP 响应，认证协议和权限策略不散落在各个路由函数中。

### 5.2 Discovery 与 JWKS

应用启动时预加载 discovery 和 JWKS。加载失败时不能启用匿名降级；在没有有效缓存时，受保护接口返回 `503`。

Discovery 和 JWKS 使用进程内缓存。遇到 Token Header 中未知的 `kid` 时刷新 JWKS，并且只重试一次，以支持 Keycloak 公钥轮换并防止无限网络重试。已有可用缓存时，Keycloak 短暂不可用不影响使用缓存公钥验证已知 `kid` 的 Token。

### 5.3 Token 验证

后端必须同时验证：

1. Bearer Token 存在且 JWT 格式合法。
2. 签名使用 discovery/JWKS 提供的公钥验证成功。
3. 算法严格限制为 RS256，不能根据 Token Header 动态放宽。
4. `iss` 精确等于 `http://10.8.6.32:18080/realms/company-dev`。
5. `exp` 存在且未过期。
6. `aud` 是字符串或数组，且包含 `rixin-contract-comparison-api`。
7. `sub` 存在且非空。

角色仅从 `resource_access.rixin-contract-comparison-api.roles` 读取。Realm Role、其他 Client 的角色或前端提交的角色均不参与本应用鉴权。

### 5.4 CurrentUser

验证成功后生成只读用户上下文，包含：

- `sub`
- `preferred_username`
- `name`
- `email`
- `department_code`
- `department_name`
- `roles`

日志可以记录 `sub`、用户名、路由和权限结果，但不得记录完整 JWT、Authorization Header、Client Secret 或用户邮箱。

## 6. 本人数据隔离

### 6.1 任务归属

创建新任务时，后端从 `CurrentUser` 写入：

- `owner_sub`
- `owner_username`
- `owner_display_name`
- `owner_department_code`
- `owner_department_name`

`owner_sub` 是不可变的资源所有者标识。其他字段是创建时的展示和审计快照，不作为唯一键，也不直接用于本期资源授权。前端不得提交或覆盖归属字段。

后台任务运行不依赖浏览器会话；任务归属在排队前已经写入任务元数据，并随任务持久化。

### 6.2 访问规则

- `agent_user` 和 `agent_manager` 只能查询、查看和操作 `owner_sub == current_user.sub` 的任务。
- `agent_admin` 可以查询和访问所有接入认证后创建的新任务。
- `agent_manager` 不自动拥有全量或部门合同访问权。
- 列表过滤在后端完成，前端不能指定其他用户的 `sub`。
- 详情、差异、质量摘要、执行状态、PDF、报告、SSE、取消、重试和审核统一使用同一个任务访问策略。
- 对不存在、无权访问或没有 `owner_sub` 的任务统一返回 `404`，避免泄露任务是否存在。
- 审核操作的 `reviewed_by` 由后端根据当前用户生成。为保持请求模型兼容，旧字段可以继续被解析，但其值必须被忽略。

### 6.3 历史任务

历史无归属任务不迁移、不补录、不展示，也不能通过旧任务 ID 直接访问，包括管理员。原始存储文件暂时保留，清理不属于本期范围。

## 7. 角色与接口权限


已认证但没有三个应用角色的用户可以查看自己此前创建的任务，但不能创建、取消、重试或审核任务。这符合“本人数据允许任意已认证用户访问”和“普通业务功能需要应用角色”的分层规则。

生产部署应关闭或通过网关限制 `/docs` 和 `/openapi.json`。该限制不改变现有业务 API。

## 8. 配置

前端环境变量：

```dotenv
VITE_OIDC_AUTHORITY=http://10.8.6.32:18080/realms/company-dev
VITE_OIDC_CLIENT_ID=rixin-contract-comparison-web
VITE_OIDC_REDIRECT_URI=http://127.0.0.1:5173/callback
VITE_OIDC_POST_LOGOUT_REDIRECT_URI=http://127.0.0.1:5173/
VITE_OIDC_SCOPE=openid profile email
```

后端环境变量：

```dotenv
OIDC_DISCOVERY_URL=http://10.8.6.32:18080/realms/company-dev/.well-known/openid-configuration
OIDC_ISSUER=http://10.8.6.32:18080/realms/company-dev
OIDC_AUDIENCE=rixin-contract-comparison-api
OIDC_RESOURCE_CLIENT_ID=rixin-contract-comparison-api
OIDC_ALLOWED_ALGORITHMS=RS256
```

本期不提供关闭认证的运行时开关。测试通过依赖注入和 FastAPI dependency override 使用本地测试验证器，不能用生产可配置的“跳过鉴权”开关。

本地开发必须使用 `http://127.0.0.1:5173`，不能混用 `localhost:5173`，否则会与 Keycloak 的 redirect URI 和 web origin 白名单不一致。

## 9. Keycloak 侧联调检查

认证管理员需要确认：

- Web Client 开启 Standard Flow。
- PKCE Method 为 S256。
- redirect URI、post logout redirect URI 和 web origin 与配置完全一致。
- Web Client 获取的 Access Token 的 `aud` 包含 `rixin-contract-comparison-api`。
- 三种应用角色位于 `rixin-contract-comparison-api` Client 下。
- 部门 Mapper 输出 `department_code` 和 `department_name`。
- Access Token 包含非空 `sub`。

返回单中的 manager 和 user 示例没有展示 `aud`。联调必须检查实际 Access Token；缺少目标 audience 时后端按设计返回 `401`，不能临时跳过 audience 校验。

## 10. 错误处理

| 状态码 | 场景 |
| --- | --- |
| 401 | 缺少 Token，Token 过期，签名、算法、issuer、audience 或必要 Claim 不合法 |
| 403 | Token 有效但缺少接口要求的角色 |
| 404 | 任务不存在、属于其他用户或是无归属历史任务 |
| 503 | Discovery/JWKS 不可用且没有有效缓存 |

认证失败信息对客户端保持稳定和克制，详细原因只写安全日志，不能回显 Token 或密钥材料。

## 11. 测试策略

### 11.1 后端测试


### 11.2 前端测试


### 11.3 联调账号

联调至少需要 `agent_admin`、`agent_manager` 和 `agent_user` 各一个账号。建议另建一个已认证但没有应用角色的账号验证 `403`。账号和密码由认证管理员通过安全渠道提供。

## 12. 验收标准


## 13. 发布与回滚

部署前先由认证管理员完成 Secret 轮换和 Keycloak 配置核验，再协调发布前后端，避免后端先强制认证而旧前端仍发送匿名请求。发布后使用三个角色账号完成登录、创建、本人访问、跨用户拒绝、管理员全量访问、PDF、报告、SSE、刷新和退出的冒烟测试。

任务新增的归属字段对存储结构是向后兼容的；回滚应用代码不会修改历史文件。由于本期不迁移旧任务，也不需要数据回滚脚本。新任务在回滚后的旧应用中可能被旧模型忽略归属字段，因此回滚期间应暂停业务写入或同步恢复带鉴权的版本，不能把旧匿名版本作为长期回滚状态。
