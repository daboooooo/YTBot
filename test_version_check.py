#!/usr/bin/env python3
import yt_dlp
import requests


# 规范化版本号，去除前导零
def normalize_version(version):
    # 分割版本号并去除每个部分的前导零
    parts = version.split('.')
    normalized_parts = [str(int(part)) if part.isdigit() else part for part in parts]
    return '.'.join(normalized_parts)


# 测试yt_dlp版本检查功能
def test_check_yt_dlp_version():
    try:
        # 获取当前安装的yt_dlp版本
        current_version = yt_dlp.version.__version__
        print(f"当前yt_dlp版本: {current_version}")

        # 获取PyPI上的最新版本
        response = requests.get('https://pypi.org/pypi/yt-dlp/json', timeout=5)
        response.raise_for_status()
        latest_version = response.json()['info']['version']
        print(f"最新yt_dlp版本: {latest_version}")

        # 规范化版本号并比较
        normalized_current = normalize_version(current_version)
        normalized_latest = normalize_version(latest_version)

        print(f"规范化后的当前版本: {normalized_current}")
        print(f"规范化后的最新版本: {normalized_latest}")

        if normalized_current < normalized_latest:
            print(f"yt_dlp版本已过时! 当前版本: {current_version}, 最新版本: {latest_version}")
            print("建议运行: pip install --upgrade yt-dlp")
            return False, f"yt_dlp版本已过时! 当前版本: {current_version}, 最新版本: {latest_version}\n" \
                "建议运行: pip install --upgrade yt-dlp"
        else:
            print("yt_dlp已是最新版本")
            return True, f"yt_dlp已是最新版本: {current_version}"
    except Exception as e:
        error_msg = f"检查yt_dlp版本时出错: {str(e)}"
        print(error_msg)
        return False, error_msg


if __name__ == "__main__":
    print("测试yt_dlp版本检查功能:")
    result, message = test_check_yt_dlp_version()
    print(f"测试结果: {result}")
    print(f"消息: {message}")
