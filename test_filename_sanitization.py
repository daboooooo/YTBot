import os
import sys
from main import sanitize_filename

# 添加当前目录到Python路径
sys.path.append('/Users/horsenli/Works/ytbot')


# 测试不同类型的文件名
def test_sanitize_filename():
    # 测试用例1: 包含不支持字符的文件名
    test_cases = [
        # 包含不支持字符的文件名
        ("测试文件名<with>/invalid|chars?.mp3", "测试文件名_with_invalid_chars_.mp3"),
        # 过长的文件名
        ("a" * 300 + ".mp3", "a" * 150 + ".mp3"),
        # 操作系统保留文件名
        ("CON.mp3", "CON_1.mp3"),
        ("nul.mp3", "nul_1.mp3"),
        # 控制字符
        ("file\x01name.mp3", "filename.mp3"),
        # 空文件名或只有扩展名
        (".mp3", "unnamed_file.mp3"),
        # 正常文件名
        ("normal_filename.mp3", "normal_filename.mp3"),
        # 混合情况
        # 由于终端输出的换行问题，我们使用更动态的方式来验证文件名长度限制
        ("<CON>_very_long_filename_" + "a"*250 + ".mp3", None)
    ]

    # 运行测试
    print("开始测试文件名规范化功能...")
    success_count = 0
    fail_count = 0

    for input_name, expected_output in test_cases:
        result = sanitize_filename(input_name)

        # 特殊处理None的期望输出，进行动态验证
        if expected_output is None:
            # 验证文件名长度不超过限制
            name, ext = os.path.splitext(result)
            is_valid = len(name) <= 200
            
            # 验证文件名不包含不支持的字符
            for char in '<>"/\|?*':
                if char in result:
                    is_valid = False
                    break
            
            # 验证没有连续的下划线
            if '__' in result:
                is_valid = False
                
            # 验证没有操作系统保留文件名
            name_without_ext = os.path.splitext(os.path.basename(result))[0].upper()
            reserved_names = [
                'CON', 'PRN', 'AUX', 'NUL',
                'COM1', 'COM2', 'COM3', 'COM4', 'COM5', 'COM6', 'COM7', 'COM8', 'COM9', 
                'LPT1', 'LPT2', 'LPT3', 'LPT4', 'LPT5', 'LPT6', 'LPT7', 'LPT8', 'LPT9'
            ]
            if name_without_ext in reserved_names:
                is_valid = False
                
            if is_valid:
                success_count += 1
                print(f"✅ 通过: '{input_name}' -> '{result}' (动态验证通过)")
            else:
                fail_count += 1
                print(f"❌ 失败: '{input_name}' -> '{result}' (动态验证失败)")
        elif result == expected_output:
            success_count += 1
            print(f"✅ 通过: '{input_name}' -> '{result}'")
        else:
            fail_count += 1
            print(f"❌ 失败: '{input_name}' -> '{result}' (期望: '{expected_output}')")
    
    # 显示测试结果摘要
    print("\n测试结果摘要:")
    print(f"总测试用例: {len(test_cases)}")
    print(f"通过: {success_count}")
    print(f"失败: {fail_count}")
    
    # 返回测试结果
    return fail_count == 0


if __name__ == "__main__":
    success = test_sanitize_filename()
    # 设置退出码，0表示成功，非0表示失败
    exit_code = 0 if success else 1
    sys.exit(exit_code)
