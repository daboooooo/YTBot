# 多角色功能测试和监督方案

## Why
YTBot 重构完成后，需要验证各功能模块在实际使用场景中的表现，确保多角色（YouTube 音频、YouTube 视频、Twitter/X 长文）功能正常工作，并建立持续监督机制。

## What Changes
- 创建系统化的功能测试方案，覆盖所有核心功能
- 实现测试监督机制，记录测试结果和问题
- 使用真实链接进行端到端测试
- 建立测试报告和问题跟踪机制

## Impact
- Affected specs: 测试流程、质量保证
- Affected code: 无代码变更，仅测试执行

## ADDED Requirements

### Requirement: YouTube 音频下载测试
系统 SHALL 正确处理 YouTube 音频下载请求，使用真实链接进行验证。

#### Scenario: YouTube 音频下载成功
- **WHEN** 用户发送测试链接 `https://youtu.be/g0eJr2nf3AE`
- **AND** 选择下载音频
- **THEN** 系统正确识别视频信息（HWASA - Good Goodbye）
- **AND** 选择最高比特率音频格式
- **AND** 成功下载音频文件
- **AND** 正确上传到存储服务（Nextcloud 或本地）
- **AND** 返回文件访问路径

#### Scenario: YouTube 音频格式选择
- **WHEN** 获取格式列表后
- **THEN** 优先选择 opus 格式（如 251）
- **OR** 选择 m4a 格式（如 140）
- **AND** 记录选中的格式 ID

### Requirement: YouTube 视频下载测试
系统 SHALL 正确处理 YouTube 视频下载请求，包括视频和音频流的合并。

#### Scenario: YouTube 视频下载成功
- **WHEN** 用户发送测试链接 `https://youtu.be/g0eJr2nf3AE`
- **AND** 选择下载视频
- **THEN** 系统正确识别视频信息
- **AND** 选择 1080p 或最高可用画质视频格式
- **AND** 选择最佳音频格式
- **AND** 同时下载视频和音频流
- **AND** 使用 ffmpeg 合并为 MP4 格式
- **AND** 正确上传到存储服务
- **AND** 返回文件访问路径

#### Scenario: YouTube 视频格式选择
- **WHEN** 获取格式列表后
- **THEN** 优先选择 1080p 视频格式（如 137）
- **OR** 选择低于 1080p 的最高画质
- **AND** 选择对应的最佳音频格式
- **AND** 使用 ffmpeg 合并视频和音频

### Requirement: Twitter/X 长文内容测试
系统 SHALL 正确抓取和处理 Twitter/X 长文内容。

#### Scenario: Twitter/X 长文抓取成功
- **WHEN** 用户发送测试链接 `https://x.com/gosailglobal/status/2023945258896351644`
- **THEN** 系统正确识别推文
- **AND** 成功抓取推文标题和完整内容
- **AND** 自动展开长文内容（如有"显示更多"）
- **AND** 过滤无关内容（analytics、广告等）
- **AND** 保留正文格式（加粗、链接等）
- **AND** 保存为 Markdown 文件
- **AND** 正确上传到存储服务

#### Scenario: Twitter/X 内容过滤
- **WHEN** 抓取推文内容时
- **THEN** 过滤掉 analytics 追踪代码
- **AND** 过滤掉广告内容
- **AND** 过滤掉推荐内容
- **AND** 保留正文文本和格式

### Requirement: 测试监督机制
系统 SHALL 提供测试监督和报告机制，记录测试过程和结果。

#### Scenario: 测试执行记录
- **WHEN** 执行测试用例时
- **THEN** 记录测试开始时间
- **AND** 记录测试步骤和中间结果
- **AND** 记录测试结束时间
- **AND** 记录测试结果（成功/失败）
- **AND** 记录错误信息（如有）

#### Scenario: 测试报告生成
- **WHEN** 所有测试完成后
- **THEN** 生成测试报告
- **AND** 统计测试通过率
- **AND** 列出失败用例和原因
- **AND** 提供改进建议

### Requirement: 错误处理和恢复测试
系统 SHALL 正确处理各种错误场景并提供恢复机制。

#### Scenario: 下载失败处理
- **WHEN** 下载过程中发生错误
- **THEN** 记录错误日志
- **AND** 清理临时文件
- **AND** 通知用户错误信息
- **AND** 提供重试选项（如适用）

#### Scenario: 存储失败处理
- **WHEN** Nextcloud 上传失败
- **THEN** 自动切换到本地存储
- **AND** 记录到缓存队列
- **AND** 通知用户存储位置

## MODIFIED Requirements

### Requirement: 无
本方案为新增测试需求，不修改现有功能。

## REMOVED Requirements

### Requirement: 无
本方案不涉及功能删除。
