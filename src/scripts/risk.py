import os
import shutil
from pathlib import Path
from datetime import datetime

# 定义文件类型映射关系
# 您可以根据需要添加或修改后缀名
FILE_CATEGORIES = {
    "📂 快捷方式": [".lnk", ".url"],
    "📂 图片素材": [".jpg", ".jpeg", ".png", ".gif", ".bmp", ".svg", ".ico", ".webp"],
    "📂 文档资料": [".docx", ".doc", ".xlsx", ".xls", ".pptx", ".ppt", ".pdf", ".txt", ".md", ".csv"],
    "📂 压缩文件": [".zip", ".rar", ".7z", ".tar", ".gz", ".iso"],
    "📂 程序与安装包": [".exe", ".msi", ".bat", ".sh"],
    "📂 代码脚本": [".py", ".java", ".cs", ".rs", ".js", ".html", ".css", ".json", ".sql", ".go"],
    "📂 视频音频": [".mp4", ".avi", ".mov", ".mp3", ".wav", ".flac"]
}

# 杂项文件夹名称
OTHER_FOLDER = "📂 其他杂项"


def get_unique_filename(destination_folder, filename):
    """
    如果目标文件夹已存在同名文件，生成一个新文件名
    例如: resume.pdf -> resume_1.pdf
    """
    base_name = filename.name
    if not (destination_folder / base_name).exists():
        return base_name

    stem = filename.stem
    suffix = filename.suffix
    counter = 1

    while (destination_folder / f"{stem}_{counter}{suffix}").exists():
        counter += 1

    return f"{stem}_{counter}{suffix}"


def clean_desktop():
    # 获取当前用户的桌面路径
    desktop_path = Path.home() / "OneDrive - TDSYNNEX\Desktop"

    # 检查路径是否存在
    if not desktop_path.exists():
        print(f"❌ 错误：找不到桌面路径 {desktop_path}")
        return

    print(f"开始整理桌面：{desktop_path}")
    print("-" * 30)

    # 获取当前脚本的文件名，防止把自己也移走了
    script_name = Path(__file__).name

    moved_count = 0

    # 遍历桌面上的所有文件
    for item in desktop_path.iterdir():
        # 跳过文件夹、跳过脚本本身、跳过隐藏文件
        if item.is_dir() or item.name == script_name or item.name.startswith('.'):
            continue

        file_ext = item.suffix.lower()
        destination_folder = None

        # 匹配文件类型
        for category, extensions in FILE_CATEGORIES.items():
            if file_ext in extensions:
                destination_folder = desktop_path / category
                break

        # 如果没有匹配到类型，归类到"其他"（可选，如果不想移动未知文件，注释掉下面两行）
        if destination_folder is None:
            destination_folder = desktop_path / OTHER_FOLDER

        # 创建目标文件夹（如果不存在）
        if not destination_folder.exists():
            destination_folder.mkdir()

        # 处理重名并移动
        try:
            new_filename = get_unique_filename(destination_folder, item)
            shutil.move(str(item), str(destination_folder / new_filename))
            print(f"✅ 已移动: {item.name} -> {destination_folder.name}/{new_filename}")
            moved_count += 1
        except Exception as e:
            print(f"❌ 移动失败 {item.name}: {e}")

    print("-" * 30)
    print(f"🎉 整理完成！共移动了 {moved_count} 个文件。")


if __name__ == "__main__":
    clean_desktop()
    input("\n按回车键退出...")