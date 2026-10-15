# Security

生产部署应配置随机密钥、强密码、准确的 `ALLOWED_HOSTS` 和 HTTPS。数据库仅在 Compose 内部网络开放。AI CLI 在宿主机执行，认证由部署者自行配置，凭据不应复制进镜像或仓库。

Worker 只需要公共网页研究能力：Codex 使用只读沙箱，Claude Code 仅开放 WebSearch/WebFetch，OpenCode 的研究 agent 仅允许 websearch/webfetch。CLI 工具权限与操作系统沙箱不同；Claude/OpenCode 不提供与 Codex 同等的 OS 文件系统隔离，建议使用专用低权限部署用户。

若发现漏洞，请使用仓库 GitHub Security Advisories 的私密报告入口；仓库维护者应在 Settings → Security 启用 Private vulnerability reporting。不要在公开 Issue 中附带 Token、账号、数据库或敏感日志。
