import collections
import glob
import json
import os
import subprocess
import sys
import threading
import time
import traceback
import unicodedata

# py2appでバンドルされた場合、同梱したTcl/Tkライブラリを使う（Homebrewが無いMacでも起動するため）
if getattr(sys, 'frozen', False) and os.environ.get('RESOURCEPATH'):
    for env_name, pattern in [('TCL_LIBRARY', 'tcl[0-9]*'), ('TK_LIBRARY', 'tk[0-9]*')]:
        found = glob.glob(os.path.join(os.environ['RESOURCEPATH'], pattern))
        if found:
            os.environ[env_name] = found[0]

import tkinter as tk
from tkinter import filedialog, messagebox, scrolledtext, ttk
import re

# Tk 9.0以降では明示的な色指定で十分対応可能

def clean_title(title):
    return re.sub(r"^\d+\.\s*", "", title)

def parse_time_to_ms(time_str):
    if not re.match(r'^\d{1,2}:\d{2}(:\d{2})?$', time_str):
        raise ValueError(f"不正な時間形式: {time_str}")
    parts = list(map(int, time_str.split(":")))
    if len(parts) == 3:
        return (parts[0] * 3600 + parts[1] * 60 + parts[2]) * 1000
    elif len(parts) == 2:
        return (parts[0] * 60 + parts[1]) * 1000
    return 0

def format_ms(ms):
    return f"{ms / 1000.0:.6f}"

def format_clock(seconds):
    """再生位置の表示（例: 2:45、1:51:05）"""
    seconds = int(seconds)
    hours, minutes, secs = seconds // 3600, seconds % 3600 // 60, seconds % 60
    return f"{hours}:{minutes:02d}:{secs:02d}" if hours else f"{minutes}:{secs:02d}"

def format_min_sec(seconds):
    """時間の長さの表示（例: 26s、4m01s、160m22s）"""
    seconds = int(seconds)
    return f"{seconds // 60}m{seconds % 60:02d}s" if seconds >= 60 else f"{seconds}s"

def format_elapsed(seconds):
    """処理時間の表示（例: 2.0s、1m05s）"""
    return f"{seconds:.1f}s" if round(seconds, 1) < 60 else format_min_sec(round(seconds))

# ffmpegの同じ種類の警告をログに表示する最大回数（チャプターごと）
MAX_SAME_WARNINGS = 3

END_LINE_HINT = (
    "テキストからJSONを作った場合は、次を確認してください:\n"
    "・最後の行に「END 3:03:10」のような終了行があるか\n"
    "・途中に「END」だけの行が混ざっていないか（END行は最後の行だけが終了として使われます）\n"
    "・時刻が上から順番に増えているか"
)

def find_invalid_chapters(chapters):
    """終了時刻が開始時刻以前、または時刻が読めないチャプターを返す"""
    problems = []
    for i, chapter in enumerate(chapters):
        number = chapter.get("id", i) + 1
        title = chapter.get("tags", {}).get("title", "chapter")
        try:
            start = float(chapter["start_time"])
            end = float(chapter["end_time"])
        except (KeyError, TypeError, ValueError):
            problems.append(f"・{number}. {title}（開始/終了時刻が読み取れません）")
            continue
        if end <= start:
            problems.append(f"・{number}. {title}（開始 {format_min_sec(start)} / 終了 {format_min_sec(end)}）")
    return problems

# ffmpegのエラーメッセージと、その日本語での説明
FFMPEG_ERROR_HINTS = [
    ("-to value smaller than -ss", "終了時刻が開始時刻以前になっています。"),
    ("No such file or directory", "ファイルが見つかりません。外付けドライブが外れていないか確認してください。"),
    ("Permission denied", "ファイルの読み書きが許可されていません。"),
    ("No space left on device", "保存先のディスクの空き容量が足りません。"),
    ("Invalid data found", "ファイルが壊れているか、対応していない形式です。"),
    ("does not contain any stream", "ファイルに音声が含まれていません。"),
    ("matches no streams", "ファイルに音声が含まれていません。"),
]

def explain_ffmpeg_error(output_lines):
    text = "\n".join(output_lines)
    for pattern, hint in FFMPEG_ERROR_HINTS:
        if pattern in text:
            return hint
    return "原因を特定できませんでした。下のffmpegのメッセージを確認してください。"

def get_ffmpeg_path():
    for path in ["/opt/homebrew/bin/ffmpeg", "/usr/local/bin/ffmpeg", "ffmpeg"]:
        if os.path.exists(path) and os.access(path, os.X_OK):
            return path
    return "ffmpeg"

def get_ffprobe_path():
    for path in ["/opt/homebrew/bin/ffprobe", "/usr/local/bin/ffprobe", "ffprobe"]:
        if os.path.exists(path) and os.access(path, os.X_OK):
            return path
    return "ffprobe"

# アプリケーションのエラーハンドリング
def handle_exception(exc_type, exc_value, exc_traceback):
    if issubclass(exc_type, KeyboardInterrupt):
        sys.__excepthook__(exc_type, exc_value, exc_traceback)
        return
    
    error_msg = ''.join(traceback.format_exception(exc_type, exc_value, exc_traceback))
    print(f"エラーが発生しました:\n{error_msg}", file=sys.stderr)
    
    # GUIが利用可能な場合は、エラーダイアログを表示
    try:
        import tkinter.messagebox as mb
        mb.showerror("エラー", f"アプリケーションエラーが発生しました:\n\n{str(exc_value)}\n\n詳細はコンソールを確認してください。")
    except:
        pass

sys.excepthook = handle_exception

try:
    root = tk.Tk()

    # Tkのデフォルトオプションを設定（テキストウィジェットのみ）
    root.option_add('*Text*background', 'white')
    root.option_add('*Text*foreground', 'black')
    root.option_add('*Text*insertBackground', 'black')

    root.title("チャプター分割ツール")
    root.geometry("600x750")

    # Dockのアイコンをクリックしたとき、Tkは最小化した画面をすべて元に戻してしまうので、
    # メインの画面だけを出す（ライブ音源の管理は最小化したままにする）
    def reopen_application():
        root.deiconify()
        root.lift()
    root.createcommand("::tk::mac::ReopenApplication", reopen_application)

except Exception as e:
    print(f"GUIの初期化に失敗しました: {e}", file=sys.stderr)
    import tkinter.messagebox as mb
    mb.showerror("エラー", f"アプリケーションの起動に失敗しました:\n{e}")
    sys.exit(1)

# macOSでウィンドウを前面に表示
root.lift()
root.attributes('-topmost', True)
root.after_idle(root.attributes, '-topmost', False)

# 処理停止フラグ
stop_flag = False

def stop_processing():
    global stop_flag
    stop_flag = True
    log("⚠️ 処理を中断します...")

progress_var = tk.DoubleVar()
progress_bar = ttk.Progressbar(root, variable=progress_var, maximum=100)
progress_bar.pack(fill="x", padx=10, pady=10)

current_label = tk.Label(root, text="現在のチャプター: 未処理")
current_label.pack(padx=10, pady=(0, 5))

output_box = scrolledtext.ScrolledText(root, wrap="word", height=12, bg="white", fg="black", insertbackground="black")
output_box.pack(padx=10, pady=10, fill="both", expand=True)

# Tk 9.1 + macOSでは、wrap="word"の欄をクリックすると短い行でもスペースごとに折り返して表示されることがあるため、
# 1行が短い曲リストの入力欄は折り返さない
text_input = scrolledtext.ScrolledText(root, wrap="none", height=12, bg="white", fg="black", insertbackground="black")
text_input.pack(padx=10, pady=(10, 5), fill="both", expand=True)

def log(msg):
    output_box.insert(tk.END, msg + "\n")
    output_box.see(tk.END)
    root.update_idletasks()

def call_on_main(func, *args, **kwargs):
    """作業用スレッドから、ダイアログなどの画面操作をメインスレッドで実行して結果を待つ"""
    if threading.current_thread() is threading.main_thread():
        return func(*args, **kwargs)
    done = threading.Event()
    result = {}
    def run():
        try:
            result["value"] = func(*args, **kwargs)
        finally:
            done.set()
    root.after(0, run)
    done.wait()
    return result.get("value")

class _MainThreadMessagebox:
    """messageboxと同じ使い方で、メインスレッドでダイアログを表示する"""
    def __getattr__(self, name):
        func = getattr(messagebox, name)
        return lambda *args, **kwargs: call_on_main(func, *args, **kwargs)

main_messagebox = _MainThreadMessagebox()

def convert_text_to_json():
    input_text = text_input.get("1.0", tk.END).strip()
    lines = [line for line in input_text.splitlines() if line.strip()]

    if not lines:
        messagebox.showwarning("警告", "テキストを入力してください。")
        return

    # 「1.」の行より上に書いた文字列は、公演名などの見出しとして保存するファイル名に使う
    first = next((i for i, line in enumerate(lines) if re.match(r"^\s*1\.", line)), 0)
    heading = " ".join(line.strip() for line in lines[:first])
    lines = lines[first:]
    if not lines:
        messagebox.showwarning("警告", "「1.」から始まる行がありません。")
        return

    final_end = None
    final_end_line = None

    try:
        # 最後の行の曲名がちょうど「END」のときだけ、全体の終了時刻として扱う
        # （「Worlds end」のような曲名や、途中のEND行を終了と取り違えないため）
        last_parts = lines[-1].strip().rsplit(" ", 1)
        if len(last_parts) == 2 and clean_title(last_parts[0].strip()).upper() == "END":
            final_end = parse_time_to_ms(last_parts[1])
            final_end_line = len(lines) - 1

        parsed = []
        for idx, line in enumerate(lines):
            if idx == final_end_line:
                continue
            parts = line.strip().rsplit(" ", 1)
            if len(parts) != 2:
                raise ValueError(f"無効な行の形式: {line}")
            title, time_str = parts
            start_ms = parse_time_to_ms(time_str)
            parsed.append((idx, title.strip(), start_ms))
    except ValueError as ve:
        messagebox.showerror("エラー", f"テキストの形式が正しくありません:\n{ve}")
        return

    chapters = []
    for i, (cid, title, start_ms) in enumerate(parsed):
        end_ms = parsed[i + 1][2] if i + 1 < len(parsed) else final_end or start_ms
        chapters.append({
            "id": cid,
            "time_base": "1/1000",
            "start": start_ms,
            "start_time": format_ms(start_ms),
            "end": end_ms,
            "end_time": format_ms(end_ms),
            "tags": {"title": clean_title(title)}
        })

    # 終了時刻が開始時刻以前のチャプターがあれば、原因を説明して保存しない
    problems = []
    for i, (cid, title, start_ms) in enumerate(parsed):
        end_ms = chapters[i]["end"]
        if end_ms > start_ms:
            continue
        label = f"・{clean_title(title)}（開始 {format_min_sec(start_ms / 1000)}）"
        relation = "と同じです" if end_ms == start_ms else "より前です"
        if i + 1 < len(parsed):
            reason = f"  次の「{clean_title(parsed[i + 1][1])}」の開始時刻 {format_min_sec(end_ms / 1000)} がこれ{relation}。時刻を確認してください。"
            if clean_title(title).upper() == "END":
                reason += "\n  END行は最後の行にだけ書けます。途中のEND行は削除してください。"
            problems.append(f"{label}\n{reason}")
        elif final_end is None:
            problems.append(f"{label}\n  最後のチャプターですが、END行が無いため終了時刻が決まりません。\n  末尾に「END 3:03:10」のような行を追加してください。")
        else:
            problems.append(f"{label}\n  END行の時刻 {format_min_sec(final_end / 1000)} がこれ{relation}。")
    if problems:
        details = "\n".join(problems)
        log(f"❌ JSONに変換できないチャプターがあります:\n{details}")
        messagebox.showerror("エラー", f"次のチャプターは終了時刻が開始時刻以前になるため、JSONを保存しませんでした。\n\n{details}")
        return

    output = {"chapters": chapters}

    # JSONファイルの保存先を選択
    desktop_path = os.path.join(os.path.expanduser("~"), "Desktop")
    # ファイル名に使えない「/」「:」は置き換える
    initial_name = re.sub(r"[/:]", "_", heading).strip() or "chapters"
    json_path = filedialog.asksaveasfilename(
        parent=root,
        title="JSONファイルを保存",
        initialdir=desktop_path,
        initialfile=f"{initial_name}.json",
        defaultextension=".json",
        filetypes=[("JSON files", "*.json"), ("All files", "*.*")]
    )

    if not json_path:
        return

    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)
    messagebox.showinfo("成功", f"JSONを保存しました:\n{os.path.basename(json_path)}")
    log(f"✅ JSON書き出し成功: {json_path}")

def extract_chapters_from_video():
    def run():
        video_path = filedialog.askopenfilename(
            parent=root,
            title="動画ファイルを選択",
            filetypes=[
                ("Video files", "*.mp4 *.mov *.avi *.mkv *.m4v"),
                ("All files", "*.*")
            ]
        )
        if not video_path:
            return

        ffprobe_path = get_ffprobe_path()
        log(f"▶ ffprobe path: {ffprobe_path}")
        log(f"📹 動画ファイル: {video_path}")

        # ffprobeでチャプター情報を抽出
        cmd = [
            ffprobe_path, "-i", video_path,
            "-print_format", "json",
            "-show_chapters",
            "-loglevel", "error"
        ]

        try:
            process = subprocess.Popen(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, encoding='utf-8', errors='replace'
            )
            stdout, stderr = process.communicate()

            if process.returncode != 0:
                error_msg = stderr.strip() if stderr else "エラーが発生しました"
                messagebox.showerror("エラー", f"ffprobeの実行に失敗しました:\n{error_msg}")
                log(f"❌ エラー: {error_msg}")
                return

            # JSONをパース
            data = json.loads(stdout)
            chapters = data.get("chapters", [])

            if not chapters:
                messagebox.showwarning("警告", "この動画ファイルにはチャプター情報が含まれていません。")
                log("⚠️ チャプター情報が見つかりませんでした")
                return

            log(f"✅ {len(chapters)}個のチャプターを検出しました")

            # JSONファイルの保存先を選択
            desktop_path = os.path.join(os.path.expanduser("~"), "Desktop")
            json_path = filedialog.asksaveasfilename(
                parent=root,
                title="JSONファイルを保存",
                initialdir=desktop_path,
                initialfile="chapters.json",
                defaultextension=".json",
                filetypes=[("JSON files", "*.json"), ("All files", "*.*")]
            )

            if not json_path:
                return

            with open(json_path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)

            messagebox.showinfo("成功", f"チャプター情報を保存しました:\n{os.path.basename(json_path)}\n（{len(chapters)}個のチャプター）")
            log(f"✅ JSON書き出し成功: {json_path}")
            log(f"📊 チャプター数: {len(chapters)}")

        except json.JSONDecodeError as e:
            messagebox.showerror("エラー", f"JSONの解析に失敗しました:\n{e}")
            log(f"❌ JSON解析エラー: {e}")
        except Exception as e:
            messagebox.showerror("エラー", f"エラーが発生しました:\n{e}")
            log(f"❌ エラー: {e}")

    run()

def split_audio_fast(media_path=None):
    # media_pathを渡された場合（ライブ音源の管理画面から呼ばれた場合）はファイル選択を省く
    if media_path is None:
        media_path = filedialog.askopenfilename(
            parent=root,
            title="音声ファイルまたは動画ファイルを選択",
            filetypes=[
                ("Audio files", "*.m4a *.mp3 *.wav"),
                ("Video files", "*.mp4 *.mov *.avi *.mkv"),
                ("All files", "*.*")
            ]
        )
    if not media_path:
        return

    # ファイルの拡張子で動画か音声かを判定
    ext = os.path.splitext(media_path)[1].lower()
    is_video = ext in ['.mp4', '.mov', '.avi', '.mkv', '.flv', '.webm', '.m4v']

    log(f"📹 選択されたファイル: {media_path}")
    log(f"📋 ファイルタイプ: {'動画' if is_video else '音声'}")

    # 動画ファイルの場合は、動画からチャプター情報を自動抽出
    if is_video:
        ffprobe_path = get_ffprobe_path()
        log(f"▶ ffprobe path: {ffprobe_path}")
        log("📊 動画からチャプター情報を抽出中...")

        cmd = [
            ffprobe_path, "-i", media_path,
            "-print_format", "json",
            "-show_chapters",
            "-loglevel", "error"
        ]

        try:
            process = subprocess.Popen(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, encoding='utf-8', errors='replace'
            )
            stdout, stderr = process.communicate()

            if process.returncode != 0:
                error_msg = stderr.strip() if stderr else "エラーが発生しました"
                messagebox.showerror("エラー", f"チャプター情報の抽出に失敗しました:\n{error_msg}")
                log(f"❌ エラー: {error_msg}")
                return

            # JSONをパース
            data = json.loads(stdout)
            chapters = data.get("chapters", [])

            if not chapters:
                log("⚠️ 動画にチャプター情報が見つかりませんでした")
                # JSONファイルを選択するか確認
                if messagebox.askyesno("チャプター情報なし",
                                      "この動画ファイルにはチャプター情報が含まれていません。\n\nJSONファイルを選択して分割しますか？"):
                    # JSONファイルを選択
                    json_path = filedialog.askopenfilename(
                        parent=root,
                        title="チャプターJSONファイルを選択",
                        filetypes=[("JSON files", "*.json")],
                        initialdir=os.path.join(os.path.expanduser("~"), "Desktop")
                    )
                    if not json_path:
                        log("❌ JSONファイルが選択されませんでした")
                        return

                    log(f"📄 JSONファイル: {json_path}")
                    with open(json_path, "r", encoding="utf-8") as f:
                        data = json.load(f)
                    chapters = data.get("chapters", [])

                    if not chapters:
                        messagebox.showerror("エラー", "JSONファイルにチャプター情報が含まれていません。")
                        log("❌ JSONファイルにチャプター情報がありません")
                        return
                    log(f"✅ JSONから{len(chapters)}個のチャプターを読み込みました")
                else:
                    log("❌ 処理をキャンセルしました")
                    return
            else:
                log(f"✅ {len(chapters)}個のチャプターを検出しました")

        except json.JSONDecodeError as e:
            messagebox.showerror("エラー", f"JSONの解析に失敗しました:\n{e}")
            log(f"❌ JSON解析エラー: {e}")
            return
        except Exception as e:
            messagebox.showerror("エラー", f"エラーが発生しました:\n{e}")
            log(f"❌ エラー: {e}")
            return

    else:
        # 音声ファイルの場合は、JSONファイルを選択
        json_path = filedialog.askopenfilename(
            parent=root,
            title="チャプターJSONファイルを選択",
            filetypes=[("JSON files", "*.json")],
            initialdir=os.path.join(os.path.expanduser("~"), "Desktop")
        )
        if not json_path:
            return
        log(f"📄 JSONファイル: {json_path}")

        try:
            with open(json_path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except json.JSONDecodeError as e:
            messagebox.showerror("エラー", f"JSONファイルの形式が正しくありません:\n{os.path.basename(json_path)}\n\n{e.lineno}行目 {e.colno}文字目: {e.msg}")
            log(f"❌ JSON解析エラー: {e}")
            return

        chapters = data.get("chapters", [])
        if not chapters:
            messagebox.showerror("エラー", "JSONファイルにチャプター情報が含まれていません。")
            log("❌ JSONファイルにチャプター情報がありません")
            return

    # 分割を始める前に、分割できないチャプターが無いか確認
    problems = find_invalid_chapters(chapters)
    if problems:
        details = "\n".join(problems)
        log(f"❌ 分割できないチャプターがあります:\n{details}")
        messagebox.showerror(
            "チャプター情報のエラー",
            f"次のチャプターは終了時刻が開始時刻以前になっているため、分割できません。\n\n{details}\n\n{END_LINE_HINT}"
        )
        return

    # ここから先の分割処理は時間がかかるため作業用スレッドで実行する
    # （ファイル選択などのダイアログは作業用スレッドから開くと固まることがあるため、ここまではメインスレッドで行う）
    def process():
        media_filename = os.path.splitext(os.path.basename(media_path))[0]
        desktop_path = os.path.join(os.path.expanduser("~"), "Desktop")
        output_dir = os.path.join(desktop_path, media_filename)
        os.makedirs(output_dir, exist_ok=True)
        log(f"💾 出力先: {output_dir}")

        ffmpeg_path = get_ffmpeg_path()
        ffprobe_path = get_ffprobe_path()
        log(f"▶ ffmpeg path: {ffmpeg_path}")

        # 元のファイルからメタデータを取得
        metadata = {}
        try:
            cmd_metadata = [
                ffprobe_path, "-i", media_path,
                "-print_format", "json",
                "-show_format",
                "-show_streams",
                "-loglevel", "error"
            ]
            process_metadata = subprocess.Popen(
                cmd_metadata, stdout=subprocess.PIPE, stderr=subprocess.PIPE, encoding='utf-8', errors='replace'
            )
            stdout_metadata, stderr_metadata = process_metadata.communicate()
            if process_metadata.returncode == 0:
                format_data = json.loads(stdout_metadata)

                # formatタグとストリームタグの両方を確認
                all_tags = {}
                format_tags = format_data.get("format", {}).get("tags", {})
                all_tags.update(format_tags)

                # ストリームタグも確認（特に音声ストリーム）
                streams = format_data.get("streams", [])
                for stream in streams:
                    if stream.get("codec_type") == "audio":
                        stream_tags = stream.get("tags", {})
                        all_tags.update(stream_tags)
                        break

                log(f"🔍 検出されたメタデータタグ: {list(all_tags.keys())}")

                # 継承するメタデータのマッピング定義
                metadata_mapping = {
                    "album": ["album", "ALBUM", "Album", "©alb"],
                    "artist": ["artist", "ARTIST", "Artist", "©ART", "album_artist", "ALBUM_ARTIST"],
                    "album_artist": ["album_artist", "ALBUM_ARTIST", "albumartist"],
                    "genre": ["genre", "GENRE", "Genre", "©gen"],
                    "date": ["date", "DATE", "Date", "year", "YEAR", "©day"],
                    "composer": ["composer", "COMPOSER", "Composer", "©wrt"],
                    "comment": ["comment", "COMMENT", "Comment", "©cmt"],
                    "copyright": ["copyright", "COPYRIGHT", "Copyright", "©cpy"],
                    "publisher": ["publisher", "PUBLISHER", "Publisher", "label"],
                    "description": ["description", "DESCRIPTION", "Description"],
                }

                # 各メタデータを検索して取得
                for meta_key, possible_keys in metadata_mapping.items():
                    for key in possible_keys:
                        if key in all_tags and all_tags[key] and str(all_tags[key]).strip():
                            metadata[meta_key] = str(all_tags[key]).strip()
                            log(f"  📌 {meta_key}: {metadata[meta_key]}")
                            break

                # 動画ファイルの場合、元のファイルのタイトルをアルバムタイトルとして設定
                if is_video:
                    # 動画のタイトルタグを取得
                    video_title = None
                    for key in ["title", "TITLE", "Title", "©nam"]:
                        if key in all_tags and all_tags[key] and str(all_tags[key]).strip():
                            video_title = str(all_tags[key]).strip()
                            break

                    # タイトルが見つかった場合、それをアルバムタイトルとして設定
                    if video_title:
                        metadata["album"] = video_title
                        log(f"  🎬 動画タイトルをアルバムに設定: {video_title}")
                    # タイトルタグがない場合はファイル名をアルバムタイトルとして使用
                    else:
                        metadata["album"] = media_filename
                        log(f"  📁 ファイル名をアルバムに設定: {media_filename}")

                if metadata:
                    log(f"📋 継承するメタデータ: {list(metadata.keys())}")
                else:
                    log(f"⚠️ メタデータが見つかりませんでした")
        except Exception as e:
            log(f"⚠️ メタデータの取得に失敗（継続します）: {e}")

        # 出力ファイルのリストを保存（音声ファイルの場合、後でアートワークを追加）
        output_files = []
        split_started = time.time()

        for i, chapter in enumerate(chapters):
            # 停止フラグをチェック
            global stop_flag
            if stop_flag:
                log("❌ 処理が中断されました")
                main_messagebox.showwarning("中断", "処理を中断しました。")
                stop_flag = False
                current_label.config(text="処理が中断されました")
                progress_var.set(0)
                return

            start = chapter["start_time"]
            end = chapter["end_time"]
            title = chapter.get("tags", {}).get("title", "chapter")
            safe_title = title.replace(" ", "_").replace("/", "_")[:50]
            chapter_id = chapter.get("id", 0)
            track_number = chapter_id + 1

            # 出力ファイルは常にm4a形式
            output_file = os.path.join(output_dir, f"{track_number:02d}_{safe_title}.m4a")
            output_files.append(output_file)

            # 動画の場合は音声のみを抽出、音声の場合はそのまま処理
            # 高品質AACエンコード（途切れのない正確な分割）
            if is_video:
                # 動画：-ss/-toを-iの前に配置（アートワーク継承に必須）
                cmd = [
                    ffmpeg_path, "-y",
                    "-ss", start,
                    "-to", end,
                    "-i", media_path,
                ]
            else:
                # 音声：-ss/-toを-iの後に配置（0057704の方法で途切れ防止）
                cmd = [
                    ffmpeg_path, "-y", "-i", media_path,
                    "-ss", start,
                    "-to", end,
                ]

            # 音声ストリームのマッピング
            cmd.extend([
                "-map", "0:a:0",  # 最初の音声ストリームのみ
                "-c:a", "aac",  # 再エンコードで正確な分割
                "-b:a", "256k",  # 高品質ビットレート
            ])

            # 動画の場合のみアートワークを含める（音声は後で一斉に追加）
            if is_video:
                # 動画の場合：ストリーム#0:2以降がattached_pic（#0:0はメインビデオ、#0:1は音声）
                cmd.extend([
                    "-map", "0:v:1?",  # 2番目のビデオストリーム（attached_pic）
                    "-map", "0:v:2?",  # 3番目のビデオストリーム（attached_pic）
                    "-c:v", "copy",  # アートワークをコピー
                    "-disposition:v", "attached_pic",  # アートワークとして設定
                ])

            cmd.extend(["-f", "mp4"])  # MP4コンテナを明示

            # メタデータをクリアしてから設定
            cmd.extend(["-map_metadata", "-1"])

            # 取得したメタデータを明示的に設定
            for meta_key, meta_value in metadata.items():
                cmd.extend(["-metadata", f"{meta_key}={meta_value}"])

            # チャプターごとのタイトルとトラック番号を設定
            cmd.extend([
                "-metadata", f"title={title}",
                "-metadata", f"track={track_number}",
                output_file
            ])

            log(f"{track_number}: {title} ({format_clock(float(start))}-{format_clock(float(end))}, {format_min_sec(float(end) - float(start))})")
            current_label.config(text=f"現在のチャプター: {track_number} - {title}")

            chapter_started = time.time()
            process = subprocess.Popen(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, encoding='utf-8', errors='replace'
            )
            ffmpeg_output = []
            # 壊れたファイルでは同じ警告が数万行出てログ表示が追いつかなくなるため、
            # 同じ種類の警告は最初の数回だけ表示し、残りは件数だけ表示する
            warning_counts = {}
            suppressed = 0
            for line in process.stdout:
                line = line.strip()
                if not line:
                    continue
                ffmpeg_output.append(line)
                if line.startswith("[") or line.startswith("Last message repeated"):
                    key = re.sub(r"0x[0-9a-f]+|-?\d+", "#", line)
                    warning_counts[key] = warning_counts.get(key, 0) + 1
                    if warning_counts[key] > MAX_SAME_WARNINGS:
                        suppressed += 1
                        continue
                log(line)
            process.wait()
            if suppressed:
                log(f"（同じ警告が続いたため {suppressed} 行を省略しました）")

            # エラーチェック
            if process.returncode != 0:
                log(f"❌ ffmpegがエラーコード{process.returncode}で終了しました")
                main_messagebox.showerror(
                    "エラー",
                    f"チャプター{track_number}「{title}」（{format_min_sec(float(start))}〜{format_min_sec(float(end))}）の処理に失敗しました。\n\n"
                    f"原因: {explain_ffmpeg_error(ffmpeg_output)}\n\n"
                    f"ffmpegのメッセージ（エラーコード{process.returncode}）:\n" + "\n".join(ffmpeg_output[-5:])
                )
                return

            log(f"-> {format_elapsed(time.time() - chapter_started)}")

            progress = ((i + 1) / len(chapters)) * 100
            progress_var.set(progress)
            root.update_idletasks()

        # 音声ファイルの場合、分割後に一斉にアートワークを追加
        if not is_video and output_files:
            log("🎨 アートワークを追加中...")
            current_label.config(text="アートワークを追加中...")

            for idx, output_file in enumerate(output_files):
                temp_file = output_file + ".temp.m4a"

                artwork_cmd = [
                    ffmpeg_path, "-y", "-hide_banner", "-loglevel", "error",
                    "-i", output_file,  # 分割済みファイル
                    "-i", media_path,   # 元のファイル（アートワーク取得用）
                    "-map", "0:a",      # 分割済みファイルの音声
                    "-map", "1:v?",     # 元のファイルのアートワーク
                    "-c:a", "copy",     # 音声はコピー
                    "-c:v", "copy",     # アートワークもコピー
                    "-disposition:v:0", "attached_pic",
                    "-map_metadata", "0",  # 分割済みファイルのメタデータを保持
                    temp_file
                ]

                process = subprocess.Popen(
                    artwork_cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, encoding='utf-8', errors='replace'
                )
                artwork_output, _ = process.communicate()

                if process.returncode == 0:
                    os.replace(temp_file, output_file)
                    log(f"  ✅ {os.path.basename(output_file)}")
                else:
                    error_lines = [l for l in artwork_output.splitlines() if l.strip()]
                    log(f"  ⚠️ {os.path.basename(output_file)} - アートワーク追加失敗: {explain_ffmpeg_error(error_lines)}")
                    for l in error_lines[-3:]:
                        log(f"     {l}")
                    if os.path.exists(temp_file):
                        os.remove(temp_file)

                progress = ((idx + 1) / len(output_files)) * 100
                progress_var.set(progress)
                root.update_idletasks()

        # 合計時間は完了ダイアログを出す前に計算する（OKを押すまでの時間を含めないため）
        log(f"分割完了 (合計 {format_elapsed(time.time() - split_started)})")
        main_messagebox.showinfo("完了", "チャプター分割が完了しました。")
        current_label.config(text="すべて完了")
        progress_var.set(0)

    threading.Thread(target=process).start()

# ---- ライブ音源の管理 ----

APP_SUPPORT_DIR = os.path.join(os.path.expanduser("~"), "Library", "Application Support", "チャプター分割ツール")
SETTINGS_PATH = os.path.join(APP_SUPPORT_DIR, "settings.json")
# メモ・手で直した状態の保存先の名前（1つ目のフォルダがあるドライブの一番上に置く）
LIVE_NOTES_FILENAME = "ライブ音源の管理データ.json"
LIVE_ARTIST_PATTERN = re.compile(r"live rec|live audio", re.I)
AUDIO_EXTENSIONS = {".m4a", ".mp3", ".wav", ".aif", ".aiff", ".flac", ".aac"}
LIVE_STATUSES = ["未分割", "分割済み", "対象外"]
# 状態ごとの色（背景色, 文字色）
LIVE_STATUS_COLORS = {
    "未分割": ("#fbd5d5", "#7a1f1f"),
    "分割済み": ("#d3f0d8", "#1f5a2c"),
    "対象外": ("#e4e4e4", "#555555"),
}
# 以前の保存先（Macごと）。初回に外付けHDDへ移す
OLD_LIVE_NOTES_PATH = os.path.join(APP_SUPPORT_DIR, "live_albums.json")
DATE_PATTERN = re.compile(r"(19|20)\d\d[.\-/]\d{1,2}[.\-/](\d{1,2}|xx)", re.I)

def normalize_name(s):
    return re.sub(r"[\s_\"'“”]+", "", unicodedata.normalize("NFKC", s).lower())

def base_artist(folder_name):
    """「B'z (live rec)」→「B'z」のように、括弧の部分を除いたアーティスト名"""
    return re.sub(r"\s*\([^)]*\)\s*$", "", unicodedata.normalize("NFC", folder_name)).strip() or folder_name

def live_date(album_name):
    """アルバム名から公演日を取り出し、並べ替えできるよう「2023.09.09」の形にそろえる（日付が無ければ空）"""
    m = re.search(r"((?:19|20)\d\d)[.\-/](\d{1,2})[.\-/](\d{1,2}|xx)", album_name, re.I)
    if not m:
        return ""
    day = m.group(3).upper() if m.group(3).lower() == "xx" else f"{int(m.group(3)):02d}"
    return f"{m.group(1)}.{int(m.group(2)):02d}.{day}"

def live_venue(text):
    """日付のすぐ後ろの文字を会場名として取り出す（「(IEM)」などの音源の印や「#01」は外す）"""
    m = re.search(r"(?:19|20)\d\d[.\-/]\d{1,2}[.\-/](?:\d{1,2}|xx)", text, re.I)
    if not m:
        return ""
    rest = text[m.end():]
    if os.path.splitext(rest)[1].lower() in AUDIO_EXTENSIONS:
        rest = os.path.splitext(rest)[0]
    rest = re.sub(r"\s*#\s*\d+\s*$", "", rest)
    rest = re.sub(r"\s*[(（][^()（）]*(IEM|RH|LINE|AUD)[^()（）]*[)）]", "", rest)
    rest = rest.strip(" _-　")
    # 「(埼玉 西武ドーム)」のように全体がかっこで囲まれていれば外し、片方だけ残ったかっこも外す
    if re.fullmatch(r"[(（][^()（）]*[)）]", rest):
        rest = rest[1:-1]
    if rest.count("(") + rest.count("（") < rest.count(")") + rest.count("）"):
        rest = rest.rstrip(")）")
    if rest.count("(") + rest.count("（") > rest.count(")") + rest.count("）"):
        rest = rest.lstrip("(（")
    return rest.strip(" _-　")

def location_label(path):
    return os.path.basename(path.rstrip("/")) or path

def load_settings():
    try:
        with open(SETTINGS_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}

def save_settings(settings):
    os.makedirs(APP_SUPPORT_DIR, exist_ok=True)
    with open(SETTINGS_PATH, "w", encoding="utf-8") as f:
        json.dump(settings, f, ensure_ascii=False, indent=2)

def load_live_roots():
    # 読み込むフォルダはMacごとに違うので初期値は持たない（初めて開いたときにフォルダ設定を出す）
    return load_settings().get("live_roots") or []

def artist_kind(folder_name):
    """「B'z (unofficial live audio)」→「unofficial live audio」のように、括弧の中の種類"""
    m = re.search(r"\(([^)]*)\)\s*$", unicodedata.normalize("NFC", folder_name))
    return m.group(1).strip().lower() if m else "（なし）"

def without_parentheses(s):
    """「君がいた夏 (25th Anniversary Day - 2017.05.10)」のような、曲名に付いたかっこの部分を除く
    （かっこの中の日付は、その曲を録った公演の日付なので、元データの判定や公演の分け方には使わない）"""
    return re.sub(r"[(（][^()（）]*[)）]", "", s)

def is_source_file(filename, album_dir):
    """分割前のフルサイズのファイルかどうかを、ファイル名で判定する
    （日付が入っている、またはアルバム名と同じ名前なら元データ。長さは分割後でも30分を超えることがあるので使わない）"""
    stem = os.path.splitext(filename)[0]
    name = re.sub(r"^\d{1,2}(-\d{1,2})?[ _.]+", "", stem)
    if DATE_PATTERN.search(without_parentheses(name)):
        return True
    return normalize_name(name) == normalize_name(album_dir)

def merge_undated_tracks(shows):
    """アルバム名に日付が無く、元データのファイル名にだけ日付がある場合、分割した曲（日付なし）が別の行に分かれてしまう。
    日付ありの公演が1つだけで、日付の無い曲の時間の合計が元データの時間の30%〜115%なら、同じ公演としてまとめる。
    （対バンで自分の出番だけ分割した場合も含める。年ごとのフォルダのように、別のイベントの録音が混ざっていて
    ずっと長くなる場合はまとめない）"""
    by_album = collections.defaultdict(list)
    for entry in shows.values():
        by_album[entry["album_key"]].append(entry)
    cache = None
    for entries in by_album.values():
        undated = [e for e in entries if not e["date"]]
        dated = [e for e in entries if e["date"]]
        if len(undated) != 1 or len(dated) != 1 or not undated[0]["tracks"] or not dated[0]["sources"]:
            continue
        if cache is None:
            cache = load_duration_cache()
        source_length = total_duration(dated[0]["sources"], cache, sources=True)
        tracks_length = total_duration(undated[0]["track_paths"], cache)
        if not source_length or not (0.3 * source_length <= tracks_length <= 1.15 * source_length):
            continue
        target, extra = dated[0], undated[0]
        for field in ("names", "sources", "track_paths", "split_times", "added_times", "source_times"):
            target[field] += extra[field]
        target["tracks"] += extra["tracks"]
        target["artists"] |= extra["artists"]
        target["folders"] += [f for f in extra["folders"] if f not in target["folders"]]
        del shows[extra["key"]]
    if cache is not None:
        save_duration_cache(cache)

def scan_live_albums(roots):
    """指定したフォルダの (live rec)・(live audio) のアルバムを読み込み、公演ごとにまとめる
    （1つのアルバムに何公演分も入っていることがあるため、ファイル名の日付で公演を分ける）"""
    shows = {}
    album_file_counts = collections.Counter()
    for base in roots:
        if not os.path.isdir(base):
            continue
        location = location_label(base)
        for artist in sorted(os.listdir(base)):
            artist_dir = os.path.join(base, artist)
            if not LIVE_ARTIST_PATTERN.search(artist) or not os.path.isdir(artist_dir):
                continue
            for album in sorted(os.listdir(artist_dir)):
                album_dir = os.path.join(artist_dir, album)
                if not os.path.isdir(album_dir):
                    continue
                files = [n for n in os.listdir(album_dir)
                         if os.path.splitext(n)[1].lower() in AUDIO_EXTENSIONS and not n.startswith("._")]
                if not files:
                    continue
                # live audio（公式の映像作品などの音源）と live rec（公演の録音）は、アルバム名が同じでも別物なので分ける
                album_key = normalize_name(album) + ("|audio" if artist_kind(artist) in ("live audio", "full live audio") else "")
                album_file_counts[album_key] += len(files)
                # 「Live Rec」「Live Recording」のように括弧の無いフォルダは、1ファイルが1つの録音
                recording_folder = artist_kind(artist) == "（なし）"
                for n in files:
                    # 公演日：ファイル名の日付（曲名のかっこの中の日付は除く）、無ければアルバム名の日付
                    date = live_date(without_parentheses(n)) or live_date(album)
                    title = ""
                    if recording_folder and not date:
                        # 日付の無い録音は1ファイル1行にして、ファイル名をタイトルとして出す
                        title = re.sub(r"^\d{1,2}(-\d{1,2})?[ _.]+", "", os.path.splitext(n)[0]).strip()
                        show_key = f"{album_key}|file:{normalize_name(title)}"
                    else:
                        show_key = album_key if date == live_date(album) else f"{album_key}|{date}"
                    entry = shows.setdefault(show_key, {"key": show_key, "album_key": album_key, "album": album, "date": date, "title": title,
                                                        "artists": set(), "folders": [], "names": [], "sources": [], "tracks": 0,
                                                        "split_times": [], "added_times": [], "source_times": [], "track_paths": []})
                    entry["artists"].add(artist)
                    if (location, album_dir) not in entry["folders"]:
                        entry["folders"].append((location, album_dir))
                    path = os.path.join(album_dir, n)
                    entry["names"].append(n)
                    try:
                        created = os.stat(path).st_birthtime
                    except (OSError, AttributeError):
                        created = None
                    if created:
                        entry["added_times"].append(created)
                    if title or is_source_file(n, album):
                        entry["sources"].append(path)
                        if created:
                            entry["source_times"].append(created)
                    else:
                        entry["tracks"] += 1
                        entry["track_paths"].append(path)
                        if created:
                            entry["split_times"].append(created)
    merge_undated_tracks(shows)
    shows_per_album = collections.Counter(e["album_key"] for e in shows.values())
    for entry in shows.values():
        # アルバムのフォルダ全体でファイルが1つだけなら、名前に関係なく分割前のファイルと見なす（DVDの音声を丸ごと1ファイルにしたものなど）
        if album_file_counts[entry["album_key"]] == 1 and entry["tracks"] == 1:
            entry["sources"] = [os.path.join(folder, n) for _, folder in entry["folders"] for n in entry["names"]]
            entry["tracks"] = 0
            entry["source_times"] = entry["split_times"]
            entry["split_times"] = []
            entry["track_paths"] = []
        # 何公演も入ったアルバムで、日付の無い曲は「日付不明」の公演にまとめる
        if not entry["date"] and not entry["title"] and shows_per_album[entry["album_key"]] > 1:
            entry["date"] = "日付不明"
        to_date = lambda t: time.strftime("%Y.%m.%d", time.localtime(t))
        # 分割日：分割した曲が作られた日のうち、いちばん多い日（分割すると全曲が同じ日にできるため。
        # あとから1曲だけ作り直した曲に引っ張られないよう、いちばん早い日にはしない）
        if entry["split_times"]:
            counts = collections.Counter(to_date(t) for t in entry["split_times"])
            entry["split_date"] = min(d for d, n in counts.items() if n == max(counts.values()))
        else:
            entry["split_date"] = ""
        # 追加日：元データが作られた日（元データが無ければ、いちばん早く作られたファイルの日）
        times = entry["source_times"] or entry["added_times"]
        entry["added_date"] = to_date(min(times)) if times else ""
        # 音源：アルバム名（何公演も入ったアルバムでは、その公演のファイル名）に IEM・LINE が入っていればそれ、それ以外は AUD
        label = entry["album"] if shows_per_album[entry["album_key"]] == 1 else " ".join([entry["album"]] + entry["names"])
        # live audio（公式の映像作品などから取った音源）には IEM・LINE・RH・AUD の区分が無い
        if all(artist_kind(a) in ("live audio", "full live audio") for a in entry["artists"]):
            entry["source_type"] = ""
        elif re.search(r"IEM", label, re.I):
            entry["source_type"] = "IEM"
        elif re.search(r"\bLINE\b", label):
            entry["source_type"] = "LINE"
        elif re.search(r"\bRH\b", label):
            entry["source_type"] = "RH"
        else:
            entry["source_type"] = "AUD"
        if entry["source_type"] == "RH":
            # RH の録音は分割しない
            entry["status"] = "対象外"
        else:
            entry["status"] = "分割済み" if entry["tracks"] else "未分割"
        locations = sorted({loc for loc, _ in entry["folders"]})
        entry["location"] = "複数" if len(locations) > 1 else locations[0]
        # 会場名：その公演の日付が入ったファイル名（元データを優先）、無ければアルバム名から取る
        named = [os.path.basename(p) for p in entry["sources"]] + entry["names"]
        entry["venue"] = next((live_venue(n) for n in named if live_date(n) == entry["date"] and live_venue(n)), "") or live_venue(entry["album"])
        # 日付も会場名も無い録音（Live Rec など）は、タイトルを出す
        if not entry["venue"] and entry["title"]:
            entry["venue"] = entry["title"]
        # 拡張子：その公演のファイルの拡張子（m4a, mp3 など）
        entry["exts"] = ", ".join(sorted({os.path.splitext(n)[1].lower().lstrip(".") for n in entry["names"]}))
    return sorted(shows.values(), key=lambda e: (sorted(e["artists"])[0], e["album"], e["date"]))

DURATION_CACHE_PATH = os.path.join(APP_SUPPORT_DIR, "duration_cache.json")

def load_duration_cache():
    try:
        with open(DURATION_CACHE_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}

def save_duration_cache(cache):
    os.makedirs(APP_SUPPORT_DIR, exist_ok=True)
    with open(DURATION_CACHE_PATH, "w", encoding="utf-8") as f:
        json.dump(cache, f, ensure_ascii=False)

def file_duration(path, cache):
    """ファイルの長さ（秒）。一度読んだファイルは、大きさと更新日時が同じなら覚えておいた値を使う"""
    try:
        st = os.stat(path)
    except OSError:
        return 0
    stamp = [st.st_size, int(st.st_mtime)]
    hit = cache.get(path)
    if hit and hit[:2] == stamp:
        return hit[2]
    try:
        import mutagen
        info = mutagen.File(path)
        length = float(info.info.length) if info is not None and info.info else 0
    except Exception:
        length = 0
    cache[path] = stamp + [length]
    return length

def total_duration(paths, cache, sources=False):
    """公演の時間。
    元データ：同じ公演の元データが何本あっても（拡張子違い・トラック番号の有無・「(映像抽出)」などの別バージョン）いちばん長い1本を使う。
             「#01」「#02」のように番号で分けて録っている場合は、番号ごとにいちばん長いものを足す。
    分割した曲：同じ名前のファイル（拡張子違い、Finderの複製で付く「 1」、MusicとPreviousの両方にあるもの）は1つとして数えて足す"""
    longest = {}
    for p in paths:
        stem = os.path.splitext(os.path.basename(p))[0]
        if sources:
            part = re.search(r"#\s*(\d+)", stem)
            key = int(part.group(1)) if part else 0
        else:
            key = normalize_name(re.sub(r"\s\d+$", "", stem))
        longest[key] = max(longest.get(key, 0), file_duration(p, cache))
    return sum(longest.values())

def live_notes_path(roots):
    """メモの保存先：1つ目のフォルダがある外付けドライブの一番上（ドライブをつないだMacならどれでも同じ内容になる）"""
    for base in roots:
        parts = os.path.abspath(base).split(os.sep)
        if len(parts) >= 3 and parts[1] == "Volumes" and os.path.isdir(os.sep.join(parts[:3])):
            return os.path.join(os.sep.join(parts[:3]), LIVE_NOTES_FILENAME)
    return os.path.join(APP_SUPPORT_DIR, LIVE_NOTES_FILENAME)

def load_live_notes(path):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        pass
    # 以前のMacごとの保存先にあれば、そちらを読む（次に保存したときに新しい場所へ移る）
    try:
        with open(OLD_LIVE_NOTES_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}

def save_live_notes(notes, path):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(notes, f, ensure_ascii=False, indent=2)

live_manager_window = None

def open_live_manager():
    global live_manager_window
    # すでに開いている（最小化している）ときは、新しく作らずにその画面を出す
    if live_manager_window is not None and live_manager_window.winfo_exists():
        live_manager_window.minimized_by_user = False
        live_manager_window.deiconify()
        live_manager_window.lift()
        live_manager_window.focus_force()
        return
    win = tk.Toplevel(root)
    live_manager_window = win
    win.title("ライブ音源の管理")

    # ほかのアプリから戻ったとき、Tk は最小化した画面まで勝手に元に戻すことがある。
    # 自分で戻したとき（Dock の画面をクリックしたときなど）はこの画面が選ばれた状態になるので、
    # 戻った直後にこの画面が選ばれていなければ、勝手に戻ったとみなして最小化し直す
    win.minimized_by_user = False
    def on_unmap(event):
        if event.widget is win and win.state() == "iconic":
            win.minimized_by_user = True
    def on_map(event):
        if event.widget is not win or not win.minimized_by_user:
            return
        def check():
            if not win.winfo_exists():
                return
            try:
                focused = win.focus_get()
            except KeyError:
                focused = None
            if focused is not None and (str(focused) == str(win) or str(focused).startswith(str(win) + ".")):
                win.minimized_by_user = False
            else:
                win.iconify()
        win.after(300, check)
    win.bind("<Unmap>", on_unmap, add="+")
    win.bind("<Map>", on_map, add="+")
    win.geometry("1500x720")
    state = {"albums": [], "sort": ("artist", False), "roots": load_live_roots()}
    state["notes_path"] = live_notes_path(state["roots"])
    notes = load_live_notes(state["notes_path"])

    bar = tk.Frame(win)
    bar.pack(fill="x", padx=10, pady=(10, 5))
    tk.Label(bar, text="検索").pack(side="left")
    search_var = tk.StringVar()
    tk.Entry(bar, textvariable=search_var, width=30).pack(side="left", padx=(5, 15))
    tk.Label(bar, text="アーティスト").pack(side="left")
    artist_filter = ttk.Combobox(bar, values=["すべて"], state="readonly", width=18)
    artist_filter.set("すべて")
    artist_filter.pack(side="left", padx=(5, 15))
    tk.Label(bar, text="種類").pack(side="left")
    kind_filter = ttk.Combobox(bar, values=["すべて"], state="readonly", width=18)
    kind_filter.set("すべて")
    kind_filter.pack(side="left", padx=(5, 15))
    tk.Label(bar, text="音源").pack(side="left")
    type_filter = ttk.Combobox(bar, values=["すべて", "IEM", "LINE", "RH", "AUD"], state="readonly", width=6)
    type_filter.set("すべて")
    type_filter.pack(side="left", padx=(5, 15))
    tk.Label(bar, text="年").pack(side="left")
    year_filter = ttk.Combobox(bar, values=["すべて"], state="readonly", width=8)
    year_filter.set("すべて")
    year_filter.pack(side="left", padx=(5, 15))
    tk.Label(bar, text="状態").pack(side="left")
    status_filter = ttk.Combobox(bar, values=["すべて"] + LIVE_STATUSES, state="readonly", width=22)
    status_filter.set("すべて")
    status_filter.pack(side="left", padx=(5, 15))
    tk.Label(bar, text="拡張子").pack(side="left")
    ext_filter = ttk.Combobox(bar, values=["すべて"], state="readonly", width=6)
    ext_filter.set("すべて")
    ext_filter.pack(side="left", padx=(5, 15))
    tk.Label(bar, text="元データ").pack(side="left")
    source_filter = ttk.Combobox(bar, values=["すべて", "あり", "なし"], state="readonly", width=6)
    source_filter.set("すべて")
    source_filter.pack(side="left", padx=(5, 15))
    tk.Label(bar, text="場所").pack(side="left")
    location_filter = ttk.Combobox(bar, values=["すべて"], state="readonly", width=14)
    location_filter.set("すべて")
    location_filter.pack(side="left", padx=(5, 15))
    # 状態のタグ（色の見本を兼ね、押すとその状態だけに絞り込む）
    tag_bar = tk.Frame(win)
    tag_bar.pack(fill="x", padx=10, pady=(0, 5))
    tag_labels = {}
    def make_tag(status):
        bg, fg = LIVE_STATUS_COLORS[status]
        label = tk.Label(tag_bar, text=status, bg=bg, fg=fg, padx=10, pady=3, cursor="hand2", relief="flat")
        label.pack(side="left", padx=(0, 6))
        label.bind("<Button-1>", lambda e: (status_filter.set("すべて" if status_filter.get() == status else status), refresh_view()))
        tag_labels[status] = label
    for s in LIVE_STATUSES:
        make_tag(s)
    count_label = tk.Label(tag_bar, text="")
    count_label.pack(side="right")
    length_label = tk.Label(tag_bar, text="", fg="#555")
    length_label.pack(side="right", padx=10)

    columns = [("status", "状態", 80), ("date", "公演日", 90), ("venue", "会場・タイトル", 240), ("type", "音源", 45), ("artist", "アーティスト", 150),
               ("album", "アルバム", 320), ("location", "場所", 90), ("length", "時間", 70), ("exts", "拡張子", 70), ("tracks", "曲数", 45), ("sources", "元データ", 70), ("added_date", "追加日", 90),
               ("split_date", "分割日", 90), ("memo", "メモ", 120)]
    frame = tk.Frame(win)
    frame.pack(fill="both", expand=True, padx=10)
    tree = ttk.Treeview(frame, columns=[c for c, _, _ in columns], show="headings", selectmode="browse")
    # 手で広げた列の幅を保つため、どの列も自動では伸び縮みさせない（はみ出した分は横にスクロールする）
    saved_widths = load_settings().get("live_column_widths", {})
    for col, label, width in columns:
        tree.heading(col, text=label, command=lambda c=col: sort_by(c))
        tree.column(col, width=saved_widths.get(col, width), anchor="w", stretch=False)
    for s, (bg, fg) in LIVE_STATUS_COLORS.items():
        tree.tag_configure(s, background=bg, foreground=fg)
    scroll = ttk.Scrollbar(frame, orient="vertical", command=tree.yview)
    xscroll = ttk.Scrollbar(frame, orient="horizontal", command=tree.xview)
    tree.configure(yscrollcommand=scroll.set, xscrollcommand=xscroll.set)
    xscroll.pack(side="bottom", fill="x")
    tree.pack(side="left", fill="both", expand=True)
    scroll.pack(side="right", fill="y")

    def save_column_widths(event=None):
        # 画面を閉じるときに、列の幅を覚えておく（次に開いたときも同じ幅で出す）
        if event is not None and event.widget is not win:
            return
        settings = load_settings()
        settings["live_column_widths"] = {col: tree.column(col, "width") for col, _, _ in columns}
        save_settings(settings)
    win.bind("<Destroy>", save_column_widths, add="+")

    def status_of(entry):
        manual = notes.get(entry["key"], {}).get("status")
        # 以前の「分割済み（元データあり／なし）」は「分割済み」として読み替える
        if manual and manual.startswith("分割済み"):
            manual = "分割済み"
        return manual or entry["status"]

    def row_values(entry):
        status = status_of(entry)
        if notes.get(entry["key"], {}).get("status"):
            status += "（手動）"
        return {"status": status, "date": entry["date"], "type": entry["source_type"], "artist": "、".join(sorted(entry["artists"])),
                "venue": entry["venue"], "album": entry["album"], "location": entry["location"], "tracks": entry["tracks"],
                "sources": f"あり（{len(entry['sources'])}）" if entry["sources"] else "なし",
                "length": format_clock(entry["length"]) if entry.get("length") else ("…" if entry.get("length") is None else ""),
                "exts": entry["exts"],
                "added_date": entry["added_date"], "split_date": entry["split_date"], "memo": notes.get(entry["key"], {}).get("memo", "")}

    def save_filters():
        """絞り込みと並べ替えを保存して、画面を閉じても次に開いたときに戻す"""
        settings = load_settings()
        settings["live_filters"] = {"search": search_var.get(), "sort": list(state["sort"]),
                                    **{name: box.get() for name, box in filter_boxes.items()}}
        save_settings(settings)

    def refresh_view(*_):
        save_filters()
        query = normalize_name(search_var.get())
        rows = []
        # タグの数は、状態以外の絞り込みを反映した数にする
        counts = {s: 0 for s in LIVE_STATUSES}
        for entry in state["albums"]:
            values = row_values(entry)
            if artist_filter.get() != "すべて" and artist_filter.get() not in {base_artist(a) for a in entry["artists"]}:
                continue
            if kind_filter.get() != "すべて" and kind_filter.get() not in {artist_kind(a) for a in entry["artists"]}:
                continue
            if year_filter.get() != "すべて" and not values["date"].startswith(year_filter.get()):
                continue
            if type_filter.get() != "すべて" and values["type"] != type_filter.get():
                continue
            if location_filter.get() != "すべて" and entry["location"] != location_filter.get():
                continue
            if ext_filter.get() != "すべて" and ext_filter.get() not in values["exts"].split(", "):
                continue
            if source_filter.get() != "すべて" and values["sources"][:2] != source_filter.get():
                continue
            if query and query not in normalize_name(values["artist"] + values["album"] + values["venue"] + values["memo"]):
                continue
            counts[status_of(entry)] += 1
            if status_filter.get() != "すべて" and status_of(entry) != status_filter.get():
                continue
            rows.append((entry, values))
        col, reverse = state["sort"]
        if col == "length":
            # 長さは「1:02:03」の文字ではなく秒数で並べる
            rows.sort(key=lambda r: r[0].get("length") or 0, reverse=reverse)
        else:
            rows.sort(key=lambda r: (r[1][col] if not isinstance(r[1][col], str) else r[1][col].lower()), reverse=reverse)
        selection = tree.selection()
        top = tree.yview()[0]
        tree.delete(*tree.get_children())
        for entry, values in rows:
            tree.insert("", "end", iid=entry["key"], values=[values[c] for c, _, _ in columns], tags=(status_of(entry),))
        # 表を作り直しても、選んでいた行とスクロール位置を保つ
        keep = [s for s in selection if tree.exists(s)]
        if keep:
            tree.selection_set(keep)
        tree.yview_moveto(top)
        for s, label in tag_labels.items():
            # 絞り込み中のタグは枠で囲んで分かるようにする
            label.config(text=f"{s}  {counts[s]}", relief="solid" if status_filter.get() == s else "flat", bd=1)
        count_label.config(text=f"表示 {len(rows)} / 全 {len(state['albums'])}")

    def sort_by(col):
        current, reverse = state["sort"]
        state["sort"] = (col, not reverse if current == col else False)
        refresh_view()

    def reload():
        count_label.config(text="読み込み中...")
        win.update_idletasks()
        missing = [p for p in state["roots"] if not os.path.isdir(p)]
        state["albums"] = scan_live_albums(state["roots"])
        location_filter["values"] = ["すべて"] + [location_label(p) for p in state["roots"]] + ["複数"]
        if missing:
            # 外付けドライブがつながっていないときと、ほかのMacで初めて開いたとき（この Mac のフォルダが無い）がある
            if messagebox.askyesno("ライブ音源の管理", "次のフォルダが見つかりません（外付けドライブがつながっていないかもしれません）:\n\n" + "\n".join(missing)
                                   + "\n\n読み込むフォルダを設定し直しますか？", parent=win):
                win.after(0, edit_folders)
        artist_filter["values"] = ["すべて"] + sorted({base_artist(a) for e in state["albums"] for a in e["artists"]}, key=str.lower)
        kind_filter["values"] = ["すべて"] + sorted({artist_kind(a) for e in state["albums"] for a in e["artists"]})
        year_filter["values"] = ["すべて"] + sorted({e["date"][:4] for e in state["albums"] if e["date"][:4].isdigit()}, reverse=True)
        ext_filter["values"] = ["すべて"] + sorted({x for e in state["albums"] for x in e["exts"].split(", ") if x})
        # 保存していた絞り込みの値が、もう一覧に無いときは「すべて」に戻す
        for box in filter_boxes.values():
            if box.get() not in box["values"]:
                box.set("すべて")
        for entry in state["albums"]:
            entry["length"] = None
        refresh_view()
        start_length_calculation()

    def start_length_calculation():
        """長さは裏で計算して、少しずつ表に反映する（初回は数十秒かかるため）"""
        state["generation"] = state.get("generation", 0) + 1
        generation = state["generation"]
        albums = list(state["albums"])
        def work():
            cache = load_duration_cache()
            for i, entry in enumerate(albums, 1):
                if generation != state["generation"] or not win.winfo_exists():
                    return
                # 元データがあれば元データの合計、無ければ分割した曲の合計
                paths = entry["sources"] or entry["track_paths"]
                entry["length"] = total_duration(paths, cache, sources=bool(entry["sources"]))
                if i % 100 == 0 or i == len(albums):
                    call_on_main(lambda n=i: (length_label.config(text=f"時間を計算中 {n}/{len(albums)}" if n < len(albums) else ""), refresh_view()))
            save_duration_cache(cache)
        threading.Thread(target=work, daemon=True).start()

    def selected():
        sel = tree.selection()
        if not sel:
            messagebox.showinfo("ライブ音源の管理", "アルバムを選んでください。", parent=win)
            return None
        return next(e for e in state["albums"] if e["key"] == sel[0])

    def open_in_finder(*_):
        entry = selected()
        if entry:
            for _, folder in entry["folders"]:
                subprocess.run(["open", folder])

    def split_source():
        entry = selected()
        if not entry:
            return
        if not entry["sources"]:
            messagebox.showinfo("ライブ音源の管理", "このアルバムには元データ（分割前のファイル）が見つかりません。", parent=win)
            return
        source = entry["sources"][0]
        if len(entry["sources"]) > 1:
            source = filedialog.askopenfilename(parent=win, title="分割する元データを選択", initialdir=os.path.dirname(entry["sources"][0]))
            if not source:
                return
        split_audio_fast(source)

    def set_status():
        entry = selected()
        if not entry:
            return
        dialog = tk.Toplevel(win)
        dialog.title("状態を変更")
        dialog.transient(win)
        tk.Label(dialog, text=entry["album"], wraplength=400).pack(padx=15, pady=(15, 5))
        choice = ttk.Combobox(dialog, values=["自動で判定"] + LIVE_STATUSES, state="readonly", width=28)
        choice.set(notes.get(entry["key"], {}).get("status") or "自動で判定")
        choice.pack(padx=15, pady=5)
        def apply():
            item = notes.setdefault(entry["key"], {"album": entry["album"]})
            if choice.get() == "自動で判定":
                item.pop("status", None)
            else:
                item["status"] = choice.get()
            save_live_notes(notes, state["notes_path"])
            dialog.destroy()
            refresh_view()
        tk.Button(dialog, text="保存", command=apply).pack(pady=(5, 15))
        dialog.grab_set()

    def edit_memo():
        entry = selected()
        if not entry:
            return
        dialog = tk.Toplevel(win)
        dialog.title("メモ")
        dialog.transient(win)
        tk.Label(dialog, text=entry["album"], wraplength=400).pack(padx=15, pady=(15, 5))
        memo = tk.Entry(dialog, width=50)
        memo.insert(0, notes.get(entry["key"], {}).get("memo", ""))
        memo.pack(padx=15, pady=5)
        def apply():
            notes.setdefault(entry["key"], {"album": entry["album"]})["memo"] = memo.get()
            save_live_notes(notes, state["notes_path"])
            dialog.destroy()
            refresh_view()
        tk.Button(dialog, text="保存", command=apply).pack(pady=(5, 15))
        dialog.grab_set()

    def edit_folders():
        dialog = tk.Toplevel(win)
        dialog.title("フォルダ設定")
        dialog.transient(win)
        tk.Label(dialog, text="読み込むフォルダ（この中の (live rec)・(live audio) のアーティストを読み込みます）").pack(padx=15, pady=(15, 5), anchor="w")
        listbox = tk.Listbox(dialog, width=70, height=6)
        for p in state["roots"]:
            listbox.insert("end", p)
        listbox.pack(padx=15, pady=5, fill="both", expand=True)
        tk.Label(dialog, text="メモと手で直した状態は、1つ目のフォルダがあるドライブに保存されます。", fg="#555").pack(padx=15, anchor="w")
        def add():
            path = filedialog.askdirectory(parent=dialog, title="読み込むフォルダを選択")
            if path and path not in listbox.get(0, "end"):
                listbox.insert("end", path)
        def remove():
            for i in reversed(listbox.curselection()):
                listbox.delete(i)
        def move_up():
            sel = listbox.curselection()
            if sel and sel[0] > 0:
                i = sel[0]; text = listbox.get(i); listbox.delete(i); listbox.insert(i - 1, text); listbox.selection_set(i - 1)
        def apply():
            roots = list(listbox.get(0, "end"))
            if not roots:
                messagebox.showwarning("フォルダ設定", "フォルダを1つ以上選んでください。", parent=dialog)
                return
            settings = load_settings(); settings["live_roots"] = roots; save_settings(settings)
            state["roots"] = roots
            new_path = live_notes_path(roots)
            if new_path != state["notes_path"]:
                # 保存先が変わる場合は、今のメモを新しい保存先にも書いておく
                if not os.path.exists(new_path) and notes:
                    save_live_notes(notes, new_path)
                notes.clear(); notes.update(load_live_notes(new_path))
                state["notes_path"] = new_path
            dialog.destroy()
            reload()
        row = tk.Frame(dialog)
        row.pack(fill="x", padx=15, pady=(5, 15))
        tk.Button(row, text="追加", command=add).pack(side="left")
        tk.Button(row, text="削除", command=remove).pack(side="left", padx=5)
        tk.Button(row, text="上へ", command=move_up).pack(side="left")
        tk.Button(row, text="保存", command=apply).pack(side="right")
        dialog.grab_set()

    buttons = tk.Frame(win)
    buttons.pack(fill="x", padx=10, pady=10)
    tk.Button(buttons, text="Finderで開く", command=open_in_finder).pack(side="left")
    tk.Button(buttons, text="元データを分割", command=split_source).pack(side="left", padx=5)
    tk.Button(buttons, text="状態を変更", command=set_status).pack(side="left", padx=5)
    tk.Button(buttons, text="メモ", command=edit_memo).pack(side="left", padx=5)
    tk.Button(buttons, text="再読み込み", command=reload).pack(side="right")
    tk.Button(buttons, text="フォルダ設定", command=edit_folders).pack(side="right", padx=5)

    tree.bind("<Double-1>", open_in_finder)

    # 右クリック（Control+クリック）のメニュー
    context_menu = tk.Menu(win, tearoff=0)
    context_menu.add_command(label="Finderで開く", command=open_in_finder)
    context_menu.add_command(label="元データを分割", command=split_source)
    context_menu.add_separator()
    context_menu.add_command(label="状態を変更", command=set_status)
    context_menu.add_command(label="メモ", command=edit_memo)
    def show_context_menu(event):
        row = tree.identify_row(event.y)
        if not row:
            return
        tree.selection_set(row)
        tree.focus(row)
        context_menu.tk_popup(event.x_root, event.y_root)
    # Tk 9 の macOS では右クリックが Button-3、以前の Tk では Button-2 で届く
    for sequence in ("<Button-2>", "<Button-3>", "<Control-Button-1>"):
        tree.bind(sequence, show_context_menu)
    # 前回の絞り込みと並べ替えを戻す
    filter_boxes = {"artist": artist_filter, "kind": kind_filter, "year": year_filter, "type": type_filter,
                    "status": status_filter, "location": location_filter, "source": source_filter, "ext": ext_filter}
    saved_filters = load_settings().get("live_filters", {})
    for name, box in filter_boxes.items():
        box.set(saved_filters.get(name) or "すべて")
    search_var.set(saved_filters.get("search", ""))
    if saved_filters.get("sort") and saved_filters["sort"][0] in {c for c, _, _ in columns}:
        state["sort"] = tuple(saved_filters["sort"])
    search_var.trace_add("write", refresh_view)
    artist_filter.bind("<<ComboboxSelected>>", refresh_view)
    kind_filter.bind("<<ComboboxSelected>>", refresh_view)
    year_filter.bind("<<ComboboxSelected>>", refresh_view)
    type_filter.bind("<<ComboboxSelected>>", refresh_view)
    status_filter.bind("<<ComboboxSelected>>", refresh_view)
    location_filter.bind("<<ComboboxSelected>>", refresh_view)
    source_filter.bind("<<ComboboxSelected>>", refresh_view)
    ext_filter.bind("<<ComboboxSelected>>", refresh_view)
    # 読み込むフォルダをまだ決めていなければ、先にフォルダ設定を出す
    win.after(100, reload if state["roots"] else edit_folders)

btn_convert = tk.Button(root, text="📝 テキスト → JSON変換", command=convert_text_to_json)
btn_convert.pack(fill="x", padx=10, pady=5)

btn_extract = tk.Button(root, text="🎬 動画からチャプター抽出", command=extract_chapters_from_video)
btn_extract.pack(fill="x", padx=10, pady=5)

btn_split = tk.Button(root, text="🎬 動画/音声 → 分割", command=split_audio_fast)
btn_split.pack(fill="x", padx=10, pady=5)

btn_stop = tk.Button(root, text="⛔ 処理を中断", command=stop_processing)
btn_stop.pack(fill="x", padx=10, pady=5)

btn_live = tk.Button(root, text="📚 ライブ音源の管理", command=open_live_manager)
btn_live.pack(fill="x", padx=10, pady=5)

try:
    root.mainloop()
except Exception as e:
    print(f"アプリケーション実行中のエラー: {e}", file=sys.stderr)
    import tkinter.messagebox as mb
    mb.showerror("エラー", f"アプリケーション実行中のエラーが発生しました:\n{e}")
    sys.exit(1)