from setuptools import setup
import os

# コード署名を無効化
os.environ['CODESIGN_ALLOCATE'] = '/usr/bin/true'

APP = ['split_gui.py']
DATA_FILES = []
OPTIONS = {
    'argv_emulation': False,
    'packages': ['tkinter'],
    'includes': ['tkinter', 'tkinter.filedialog', 'tkinter.messagebox', 'tkinter.scrolledtext', 'tkinter.ttk'],
    'excludes': [],
    'plist': {
        'CFBundleName': 'チャプター分割ツール',
        'CFBundleDisplayName': 'チャプター分割ツール',
        'CFBundleGetInfoString': "動画・音声ファイルをチャプターごとに分割",
        'CFBundleIdentifier': "com.audiochaptersplitter.app",
        'CFBundleVersion': "1.0.3",
        'CFBundleShortVersionString': "1.0.3",
        'NSHighResolutionCapable': True,
        'LSMinimumSystemVersion': '10.13.0',
        'NSAppearance': 'NSAppearanceNameAqua',
    },
    'emulate_shell_environment': True,
}

setup(
    app=APP,
    name='AudioChapterSplitter',
    data_files=DATA_FILES,
    options={'py2app': OPTIONS},
    setup_requires=['py2app'],
)
