#!/usr/bin/env python3
"""
使用浏览器自动化获取真实内容并保存到Notion的脚本
"""

import subprocess
import json
import os
import sys
from notion_client import Client


def extract_content_with_js(url):
    """使用JavaScript/Node.js脚本提取X内容"""
    try:
        # 获取当前脚本所在目录
        script_dir = os.path.dirname(os.path.abspath(__file__))

        # 运行JavaScript脚本提取内容
        result = subprocess.run(
            ['node', 'x_content_scraper.js', url],
            capture_output=True,
            text=True,
            cwd=script_dir,
            timeout=60
        )

        if result.returncode == 0:
            # 解析输出的JSON
            output_lines = result.stdout.strip().split('\n')
            json_str = output_lines[-1]  # JSON输出在最后一行
            data = json.loads(json_str)

            if data['success']:
                return data['title'], data['content']
            else:
                print(f"JavaScript脚本提取失败: {data.get('error', 'Unknown error')}")
                return None, None
        else:
            print(f"JavaScript脚本运行失败: {result.stderr}")
            return None, None

    except subprocess.TimeoutExpired:
        print("JavaScript脚本运行超时")
        return None, None
    except Exception as e:
        print(f"提取过程中出错: {str(e)}")
        return None, None


def save_to_notion_with_real_content(token, database_id, title_property, original_url, title, content):
    """将真实提取的内容保存到Notion"""
    # 初始化Notion客户端
    notion = Client(auth=token)

    # 添加原始链接信息
    full_content = f"{content}\n\n---\n原始链接: {original_url}\n提取时间: {__import__('datetime').datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"

    # 准备页面内容
    children = []

    # 添加内容段落
    # 由于Notion API对单个块的长度有限制，我们需要分割长文本
    paragraphs = full_content.split('\n\n')
    for paragraph in paragraphs:
        if paragraph.strip():  # 忽略空段落
            # 如果段落太长，进一步分割（Notion rich text content限制约为2000字符）
            max_chunk_size = 2000
            while len(paragraph) > max_chunk_size:
                chunk = paragraph[:max_chunk_size]
                paragraph = paragraph[max_chunk_size:]

                children.append({
                    "object": "block",
                    "type": "paragraph",
                    "paragraph": {
                        "rich_text": [
                            {
                                "type": "text",
                                "text": {
                                    "content": chunk
                                }
                            }
                        ]
                    }
                })

            # 添加剩余部分（如果有的话）
            if paragraph.strip():
                children.append({
                    "object": "block",
                    "type": "paragraph",
                    "paragraph": {
                        "rich_text": [
                            {
                                "type": "text",
                                "text": {
                                    "content": paragraph.strip()
                                }
                            }
                        ]
                    }
                })

    # 创建新页面
    new_page = notion.pages.create(
        parent={"database_id": database_id},
        properties={
            title_property: {  # 使用传入的属性名称
                "title": [
                    {
                        "text": {
                            "content": title
                        }
                    }
                ]
            }
        },
        children=children
    )

    return new_page['id']


def main():
    if len(sys.argv) < 2:
        print("用法: python x_to_notion_with_real_content.py <X链接> [标题属性名]")
        print("注意: 请先设置环境变量 NOTION_TOKEN 和 NOTION_DATABASE_ID")
        return

    url = sys.argv[1]
    title_property = sys.argv[2] if len(sys.argv) > 2 else "Name"

    print(f"正在从X链接提取真实内容: {url}")

    # 从环境变量获取Notion凭据
    token = os.getenv('NOTION_TOKEN')
    database_id = os.getenv('NOTION_DATABASE_ID')

    if not token:
        print("错误: 请设置环境变量 NOTION_TOKEN")
        return

    if not database_id:
        print("错误: 请设置环境变量 NOTION_DATABASE_ID")
        return

    # 提取真实内容
    title, content = extract_content_with_js(url)

    if title and content:
        print(f"成功提取内容:")
        print(f"标题: {title}")
        print(f"内容: {content}")

        print(f"\n正在保存到Notion数据库...")
        page_id = save_to_notion_with_real_content(token, database_id, title_property, url, title, content)

        if page_id:
            print(f"成功保存到Notion!")
            print(f"页面ID: {page_id}")
            print(f"标题: {title}")
        else:
            print("保存到Notion失败")
    else:
        print("无法提取真实内容")


if __name__ == "__main__":
    main()