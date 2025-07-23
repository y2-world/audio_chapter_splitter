import json
import os
import subprocess
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, scrolledtext, ttk

import re

def clean_title(title):
    return re.sub(r"^\d+\.\s*", "", title)

root = tk.Tk()
root.title("オーディオチャプター分割ツール")
root.geometry("600x750")

progress_var = tk.DoubleVar()
progress_bar = ttk.Progressbar(root, variable=progress_var, maximum=100)
progress_bar.pack(fill="x", padx=10, pady=10)

current_label = tk.Label(root, text="現在のチャプター: 未処理")
current_label.pack(padx=10, pady=(0, 5))

output_box = scrolledtext.ScrolledText(root, wrap="word", height=12)
output_box.pack(padx=10, pady=10, fill="both", expand=True)

text_input = scrolledtext.ScrolledText(root, wrap="word", height=12)
text_input.pack(padx=10, pady=(10, 5), fill="both", expand=True)

def log(msg):
    output_box.insert(tk.END, msg + "\n")
    output_box.see(tk.END)
    root.update_idletasks()

def parse_time_to_ms(time_str):
    parts = list(map(int, time_str.split(":")))
    if len(parts) == 3:
        return (parts[0] * 3600 + parts[1] * 60 + parts[2]) * 1000
    elif len(parts) == 2:
        return (parts[0] * 60 + parts[1]) * 1000
    return 0

def format_ms(ms):
    return f"{ms / 1000.0:.6f}"

def convert_text_to_json():
    input_text = text_input.get("1.0", tk.END).strip()
    lines = [line for line in input_text.splitlines() if line.strip()]

    if not lines:
        messagebox.showwarning("警告", "テキストを入力してください。")
        return

    chapters = []
    final_end = None
    final_end_line = None

    for i, line in enumerate(lines):
        words = line.strip().split()
        if len(words) >= 2 and words[-2].upper() == "END":
            time_part = words[-1]
            final_end = parse_time_to_ms(time_part)
            final_end_line = i
            break

    parsed = []
    for idx, line in enumerate(lines):
        if idx == final_end_line:
            continue
        parts = line.strip().rsplit(" ", 1)
        if len(parts) != 2:
            continue
        title, time_str = parts
        start_ms = parse_time_to_ms(time_str)
        parsed.append((idx, title.strip(), start_ms))

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

    output = {"chapters": chapters}

    save_path = filedialog.asksaveasfilename(
        defaultextension=".json", filetypes=[("JSON files", "*.json")]
    )
    if save_path:
        with open(save_path, "w", encoding="utf-8") as f:
            json.dump(output, f, indent=2, ensure_ascii=False)
        messagebox.showinfo("成功", f"JSONを書き出しました:\n{save_path}")
        log(f"✅ JSON書き出し成功: {save_path}")

# macOSなどでffmpegのパスを探す（環境によって違うので適宜書き換え）
def get_ffmpeg_path():
    for path in ["/usr/local/bin/ffmpeg", "/opt/homebrew/bin/ffmpeg", "ffmpeg"]:
        if os.path.exists(path) and os.access(path, os.X_OK):
            return path
    return "ffmpeg"  # PATHにあると仮定して最後に返す

def split_audio_fast():
    def run():
        audio_path = filedialog.askopenfilename(
            title="音声ファイルを選択",
            filetypes=[("Audio files", "*.m4a *.mp3 *.wav"), ("All files", "*.*")]
        )
        if not audio_path:
            return

        json_path = filedialog.askopenfilename(
            title="チャプターJSONファイルを選択",
            filetypes=[("JSON files", "*.json")]
        )
        if not json_path:
            return

        output_dir = filedialog.askdirectory(title="分割ファイルの保存先フォルダを選択")
        if not output_dir:
            return

        ffmpeg_path = get_ffmpeg_path()
        log(f"▶ ffmpeg path: {ffmpeg_path}")

        with open(json_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        chapters = data.get("chapters", [])

        for i, chapter in enumerate(chapters):
            start = chapter["start_time"]
            end = chapter["end_time"]
            title = chapter.get("tags", {}).get("title", "chapter")
            safe_title = title.replace(" ", "_").replace("/", "_")

            chapter_id = chapter.get("id", 0)
            track_number = chapter_id + 1

            ext = os.path.splitext(audio_path)[1]
            output_file = os.path.join(output_dir, f"{track_number:02d}_{safe_title}{ext}")

            cmd = [
                ffmpeg_path, "-y", "-i", audio_path,
                "-ss", start,
                "-to", end,
                "-vn",
                "-c:a", "copy",
                "-metadata", f"title={title}",
                "-metadata", f"track={track_number}",
                output_file
            ]

            log(f"▶️ {track_number}: {title}")
            current_label.config(text=f"現在のチャプター: {track_number} - {title}")

            process = subprocess.Popen(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, universal_newlines=True
            )
            for line in process.stdout:
                if line.strip():
                    log(line.strip())
            process.wait()

            progress = ((i + 1) / len(chapters)) * 100
            progress_var.set(progress)
            root.update_idletasks()

        messagebox.showinfo("完了", "チャプター分割が完了しました。")
        log("✅ 分割完了")
        current_label.config(text="すべて完了")
        progress_var.set(0)

    threading.Thread(target=run).start()

btn_convert = tk.Button(root, text="📝 テキスト → JSON変換", command=convert_text_to_json)
btn_convert.pack(fill="x", padx=10, pady=5)

btn_split = tk.Button(root, text="🎧 JSON + 音声 → 分割（高速版）", command=split_audio_fast)
btn_split.pack(fill="x", padx=10, pady=10)

root.mainloop()