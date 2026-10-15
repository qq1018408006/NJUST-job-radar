# Contributing

欢迎提交数据源适配器、解析修复、界面改进与文档补充。项目使用 MIT 许可证。

在 Python 3.13+ 环境中安装 `requirements.txt`，运行：

```bash
python manage.py check
python manage.py makemigrations --check --dry-run
python manage.py test
```

AI Worker 只依赖 Python 标准库。CLI 后端代码在 `tools/research_providers.py`，Prompt 在 `prompts/employer_research.txt`，输出结构在 `research.schema.json`。修改后端时需验证结果、错误处理、实际工具信息与 Token 口径；自动化测试不得调用付费模型或真实招聘接口。

新数据源应独立适配到现有模型，通过 `source + source_item_id` 保持幂等，并为新增、变化、缺失和重试提供测试。宣讲会、招聘会、岗位公告的字段语义不同；新增栏目需说明对应日期和状态的含义。

PR 请说明解决的问题、最终行为和验证结果。不要提交 `.env`、数据库、账号状态、CLI 认证、执行日志或真实个人信息；测试使用虚构数据。

发布时运行 `python tools/package_release.py`。首次开源请以生成的干净目录为仓库根目录，不要上传整个 Codex 工作区或旧部署包。
