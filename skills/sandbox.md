# Sandbox
Summary: 在隔离 Docker 沙盒中创建、修改、测试并交付项目。

1. 用 `sandbox_list` 看是否已有适合本会话的沙盒；没有再 `sandbox_create`。
2. 用 `say` 告知已经开始和每个真实里程碑，不要重复没有新信息的进度。
3. 群文件先 `list_recent_files` 或 `get_message_by_id` 定位，再 `import_file_to_sandbox`。
4. 所有写入、依赖安装、构建和测试都在 `/workspace` 内完成。
   NixOS 部署使用高级统一镜像：已有 Git、curl、rg、编译器、
   Python/Node/Go/Rust/Java、Poppler/qpdf/LibreOffice/Pandoc、FFmpeg、
   ImageMagick、Tesseract、数据分析库和数据库客户端。先用
   `command -v` 或 Python import 检查，不要一上来就重复安装。
5. 先运行测试或最小启动检查，再声称完成。工具失败时报告实际错误，不可伪造成功。
6. 交付用 `send_file_from_sandbox` 或 `send_image_from_sandbox`；不再需要时销毁沙盒。
7. 沙盒不是公网部署环境，也不能访问宿主机、机器人密钥或其他用户的数据。
