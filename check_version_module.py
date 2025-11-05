import inspect
import yt_dlp.version

print("yt_dlp.version模块的属性:")
print(dir(yt_dlp.version))

# 尝试直接打印version模块的内容
print("\nversion模块的内容:")
print(inspect.getsource(yt_dlp.version))
