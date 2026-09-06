# Sandbox
Summary: 在隔离 Docker 沙盒中创建、修改、测试并交付项目。

1. 用 `sandbox_list` 看是否已有适合本会话的沙盒；没有再 `sandbox_create`。
2. 用 `say` 告知已经开始和每个真实里程碑，不要重复没有新信息的进度。
3. 群文件先 `list_recent_files` 或 `get_message_by_id` 定位，再 `import_file_to_sandbox`。
4. 所有写入、依赖安装、构建和测试都在 `/workspace` 内完成。
   NixOS 部署使用精简的 Nix 高级镜像：已有常用 shell 工具、Git、curl、
   rg、GCC、Python、Node、SQLite、Poppler 和 qpdf。先用 `command -v`
   或 Python import 检查。缺少 Go、Rust、Java、LibreOffice、Pandoc、
   FFmpeg、ImageMagick、Tesseract 或数据分析库时，先用 `nix_search`
   找到属性名，再放进 `sandbox_exec.packages`；不要使用 apt，也不要全局
   pip install。第一次会下载，之后从 Kennethbot 的共享 Nix 缓存复用。
   含中文的 PDF 统一使用 `kennethbot-pdf input.md output.pdf`，随后运行
   `pdffonts output.pdf` 确认字体已嵌入，并用 `pdftotext output.pdf -`
   确认中文可提取。不要用默认 Helvetica 生成中文 PDF。
5. 先运行测试或最小启动检查，再声称完成。工具失败时报告实际错误，不可伪造成功。
6. 交付用 `send_file_from_sandbox` 或 `send_image_from_sandbox`；不再需要时销毁沙盒。
7. 沙盒不是公网部署环境，也不能访问宿主机、机器人密钥或其他用户的数据。
8. `packages` 只对本次命令生效。执行沙盒对共享 Nix 缓存只有读取权限；
   下载由参数受限的辅助容器完成，不能把任意安装脚本伪装成软件包名。
