# X内容提取器 (X Content Extractor)

一个成功绕过X（Twitter）反爬虫保护的内容提取工具，能够获取真实的推文内容并保存到本地（Markdown + HTML格式）。

## 功能特性

- ✅ 绕过X反爬虫保护
- ✅ 自动展开长文（点击"显示更多"）
- ✅ 保留格式信息（加粗、链接、代码块、斜体）
- ✅ 同时输出 Markdown 和 HTML 格式
- ✅ 文件命名：日期 + 原标题

## 背景问题

X（Twitter）实施了严格的反爬虫机制，当通过自动化工具访问时，会检测到JavaScript被禁用，并返回提示："我们检测到此浏览器中禁用了JavaScript。请启用JavaScript或切换到受支持的浏览器以继续使用x.com。"

## 解决方案

我们采用浏览器自动化技术成功绕过了这些保护措施：

### 1. 使用Playwright浏览器自动化
- 模拟真实浏览器环境
- 设置合适的用户代理和浏览器指纹
- 处理反自动化检测

### 2. 浏览器配置技巧
- 使用无头模式运行（适合服务器环境）
- 添加多个Chrome启动参数来模拟真实环境
- 隐藏自动化特征

### 3. 关键技术要点
- 使用 `addInitScript` 在页面创建前注入脚本来隐藏自动化特征
- 设置真实的用户代理、时区、语言等信息
- 使用 `waitForSelector` 等待页面元素加载
- 合理的超时设置

## 文件说明

- `x_content_scraper.js`: 核心JavaScript脚本，使用Playwright绕过反爬虫保护
- `x_to_notion_with_real_content.py`: Python脚本，整合内容提取和Notion保存功能（可选）
- `package.json`: Node.js依赖配置

## 安装依赖

```bash
cd x_content_extractor
npm install
npx playwright install chromium
```

## 使用方法

### 仅提取内容（输出JSON）
```bash
node x_content_scraper.js "https://x.com/username/status/xxxxx"
```

### 提取并保存到本地
```bash
node x_content_scraper.js "https://x.com/username/status/xxxxx" --save-local
```

### 指定输出目录
```bash
node x_content_scraper.js "https://x.com/username/status/xxxxx" --save-local --output-dir ./output
```

### 使用 npm scripts
```bash
npm run save -- "https://x.com/username/status/xxxxx"
```

## 输出文件

使用 `--save-local` 参数时，会生成两个文件：

| 格式 | 文件名示例 | 说明 |
|------|-----------|------|
| Markdown | `2024-01-15_文章标题.md` | 适合笔记软件阅读 |
| HTML | `2024-01-15_文章标题.html` | 保留原始HTML格式，可直接浏览器打开 |

### Markdown 输出示例

```markdown
# 文章标题

> 原始链接: https://x.com/xxx/status/xxx
> 提取时间: 2024/1/15 10:30:00

---

这是正文内容，**加粗文本**，*斜体文本*，`代码块`，[链接文本](https://example.com)。
```

## 技术细节

### JavaScript/Playwright配置要点
1. 启动参数设置：包含多个参数来模拟真实浏览器
2. 页面加载策略：使用 `networkidle` 并设置适当超时
3. 元素选择器：使用X平台特有的选择器来定位内容
4. 反检测措施：通过 `addInitScript` 在创建页面前隐藏自动化特征
5. 长文展开：自动检测并点击"显示更多"按钮
6. 格式提取：提取加粗、斜体、代码、链接等格式信息

## 环境要求

- Node.js v14+
- Playwright浏览器依赖
- 系统图形库（如libatk1.0-0等）

## 注意事项

1. 请遵守X的使用条款和robots.txt
2. 不要过于频繁地请求，避免被封IP
3. 个人使用，请勿用于大规模商业抓取
4. 代码仅供学习和技术研究使用

## 许可证

MIT License

---

## 更新日志

### v1.0.0 (2024-01-15)

**修复问题：**
- 修复 `addInitScript` 调用时机错误（应在创建 page 前调用）

**新增功能：**
- 添加长文展开功能（自动点击"显示更多"）
- 添加格式信息提取（加粗、斜体、代码块、链接）
- 添加本地保存功能（Markdown + HTML 双格式输出）
- 文件命名规则：日期 + 原标题
- 创建 package.json 依赖管理文件
