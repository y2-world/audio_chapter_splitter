from setuptools import setup

APP = ['split_gui.py']
DATA_FILES = []
OPTIONS = {
    'argv_emulation': True,
    'packages': ['tkinter'],
    'iconfile': None,
    'excludes': ['setuptools', 'pkg_resources'],
    'strip': False,  # strip処理を無効化して権限エラーを回避
    'semi_standalone': False,  # 完全スタンドアロンモード
    'site_packages': False,  # システムのsite-packagesを使わない
    'plist': {
        'CFBundleName': 'AudioChapterSplitter',
        'CFBundleDisplayName': 'チャプター分割ツール',
        'CFBundleGetInfoString': "動画・音声ファイルをチャプターごとに分割",
        'CFBundleIdentifier': "com.audiochaptersplitter.app",
        'CFBundleVersion': "1.0.0",
        'CFBundleShortVersionString': "1.0.0",
        'NSHighResolutionCapable': True,
    }
}

setup(
    app=APP,
    name='AudioChapterSplitter',
    data_files=DATA_FILES,
    options={'py2app': OPTIONS},
    setup_requires=['py2app'],
)
