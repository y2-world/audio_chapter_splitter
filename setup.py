from setuptools import setup

APP = ['split_gui.py']
DATA_FILES = []
OPTIONS = {
    'plist': {
        'CFBundleName': 'チャプター分割ツール',
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
