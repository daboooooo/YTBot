import yt_dlp

print("yt_dlp模块的属性:")
print(dir(yt_dlp))

print("\n尝试获取版本信息:")
try:
    print(f'yt_dlp.version: {yt_dlp.version}')
except Exception as e:
    print(f'访问yt_dlp.version错误: {e}')

print("\n其他可能的版本获取方式:")
try:
    # 尝试直接调用函数获取版本
    print(f'模块文件: {yt_dlp.__file__}')
except Exception as e:
    print(f'获取文件路径错误: {e}')

# 尝试使用另一种常见方式
try:
    # 一些模块使用这种方式
    from pkg_resources import get_distribution
    version = get_distribution('yt_dlp').version
    print(f'通过pkg_resources获取版本: {version}')
except Exception as e:
    print(f'通过pkg_resources获取版本错误: {e}')
