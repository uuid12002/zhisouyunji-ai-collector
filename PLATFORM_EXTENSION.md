# 其他 AI 平台的扩展

## 用户预留入口

打开“平台登录与扩展”→“预留其他 AI 平台”，填写平台名称、英文编号和 HTTPS 官网地址。信息保存在客户端目录的 `platform-catalog.json`，可在页面移除待适配记录。预留项显示在平台管理和采集页提示中，不能勾选、登录或提交任务。配置中的 available、modes 不会授予执行权限。

预留仅用于平台规划，不代表填入 URL 后可以自动采集，也不修改已支持平台地址。密钥、Cookie、登录密码不得填写在平台地址中。

## 开发者接入步骤

1. 在 `collector/domain/platforms.py` 的 Platform、PLATFORM_NAMES、PLATFORM_URLS 中登记受支持标识和地址。完成适配并准备启用时再登记；界面使用统一平台目录，不新增特定平台按钮。
2. 在 `adapters.json` 配置新会话、问题输入、发送、回答、生成结束、分享和复制按钮规则。平台特殊行为封装于浏览器/证据基础设施，应用控制器不写 DOM 或 Windows 输入逻辑。
3. 需要 RPA 时，验证操作链路和真实桌面输入，再更新 `domain/interaction.py` 的支持范围。只提供浏览器适配的平台不能声明支持 RPA。
4. 补充模拟页面：新会话隔离、问题唯一发送、回答结束、全文、完整长图、点击分享后复制链接、人工验证、异常现场与中断不重发。真实登录验收与模拟测试分开记录。
5. 更新 HTTP/MCP 能力和各入口限制。批次报告与结果访问的项目编号白名单目前仍有四个平台的正则，扩展时同步调整 `presentation/batch_report.py`、`infrastructure/client_runtime.py`、`infrastructure/agent_queue.py` 等校验，并补路径边界测试。
6. 完成后移除同编号预留记录，验证旧任务、已有登录配置和幂等记录兼容，再发布新 EXE。

## 分层边界

- `application/platform_catalog.py`：目录合并、输入验证、待适配状态强制约束。
- `application/desktop.py`：平台预留、历史查询和结果访问用例，通过 runtime 端口操作。
- `infrastructure/client_runtime.py`：元数据文件、历史结果、导出文件与链接读取。
- `presentation/workbench.py`：平台管理及页面导航；`batch_dialog.py` 只展示可执行平台选择。

受支持平台仍使用受控白名单。后续可以在此边界内增加独立适配器注册机制；当前没有开放任意脚本、任意网页或远程插件执行。
