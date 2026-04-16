import cv2
import os
import numpy as np
from ultralytics import YOLO
# from paddleocr import PaddleOCR
from openai import AzureOpenAI
import base64
from decouple import config
from ultralytics.utils import LOGGER
import json
import time, random, subprocess, requests, cv2

LOGGER.setLevel("ERROR")


def env(key, default=None):
    return os.getenv(key) or config(key, default=default)

# OUT_DIR = "frames/1_HairDryer"
VIDEO_PATH = "uploadedvideo/1_HairDryer.mp4"

# OUT_DIR = "frames/2_Socks"
# VIDEO_PATH = "uploadedvideo/2_Socks.mp4"

# VIDEO_PATH = "uploadedvideo/3_BeautyCream_Puff.mp4"
# VIDEO_PATH = "uploadedvideo/4_UV_Stick_StemCell_Serum.mp4"
# VIDEO_PATH = "uploadedvideo/RPReplay_Final1763567211.mp4"

OUT_DIR = None   # sẽ set sau STEP 0

MAX_FALLBACK = 20

MAX_RETRY = 10
SLEEP_BETWEEN_REQUESTS = 25


AUDIO_OUT_ROOT = "audio"
AUDIO_TEXT_ROOT = "audio_text"
FFMPEG_BIN = env("FFMPEG_PATH", "/opt/homebrew/bin/ffmpeg")  # Mac path
CHUNK_SECONDS = 300

WHISPER_ENDPOINT = env("WHISPER_ENDPOINT")
AZURE_KEY = env("AZURE_OPENAI_KEY")

OPENAI_API_KEY = env("AZURE_OPENAI_KEY")
AZURE_OPENAI_ENDPOINT = env("AZURE_OPENAI_ENDPOINT")
GPT5_API_VERSION = env("GPT5_API_VERSION")
GPT5_MODEL = env("GPT5_MODEL")

AZURE_OPENAI_ENDPOINT_EMBED=env("AZURE_OPENAI_ENDPOINT_EMBED")
AZURE_OPENAI_API_VERSION_EMBED=env("AZURE_OPENAI_API_VERSION_EMBED")
EMBED_MODEL=env("EMBED_MODEL")

GROUP_ROOT = "group"
GROUP_FILE = "groups.json"
COSINE_THRESHOLD = 0.85

BEST_PHASE_FILE = os.path.join(GROUP_ROOT, "group_best_phases.json")
TOP_K = 1

client = AzureOpenAI(
    api_key=OPENAI_API_KEY,
    azure_endpoint=AZURE_OPENAI_ENDPOINT,
    api_version=GPT5_API_VERSION
)

embed_client = AzureOpenAI(
    api_key=OPENAI_API_KEY,
    azure_endpoint=AZURE_OPENAI_ENDPOINT_EMBED,
    api_version=AZURE_OPENAI_API_VERSION_EMBED
)

# =============================
# STEP 0 – EXTRACT FRAMES
# =============================

def extract_frames_step0(
    video_path,
    fps=1,
    frames_root="frames"
):
    video_name = os.path.splitext(os.path.basename(video_path))[0]
    out_dir = os.path.join(frames_root, video_name)
    os.makedirs(out_dir, exist_ok=True)

    cap = cv2.VideoCapture(video_path)
    video_fps = cap.get(cv2.CAP_PROP_FPS)

    sec = 0
    idx = 0

    while cap.isOpened():
        cap.set(cv2.CAP_PROP_POS_MSEC, sec * 1000)
        ret, frame = cap.read()
        if not ret:
            break

        out_path = os.path.join(
            out_dir,
            f"frame_{idx:04d}_{sec}s.jpg"
        )
        cv2.imwrite(out_path, frame)

        sec += fps
        idx += 1

    cap.release()
    print(f"[OK] Frame extraction done → {out_dir}")

    return out_dir


# =============================
# SCORE FUNCTIONS (Bước 1.1)
# =============================

def hist_diff_score(img1, img2):
    img1_hsv = cv2.cvtColor(img1, cv2.COLOR_BGR2HSV)
    img2_hsv = cv2.cvtColor(img2, cv2.COLOR_BGR2HSV)

    hist1 = cv2.calcHist([img1_hsv], [0, 1], None, [50, 60], [0, 180, 0, 256])
    hist2 = cv2.calcHist([img2_hsv], [0, 1], None, [50, 60], [0, 180, 0, 256])

    cv2.normalize(hist1, hist1)
    cv2.normalize(hist2, hist2)

    return cv2.compareHist(hist1, hist2, cv2.HISTCMP_CORREL)


def absdiff_score(img1, img2):
    img1_small = cv2.resize(img1, (256, 256))
    img2_small = cv2.resize(img2, (256, 256))

    diff = cv2.absdiff(img1_small, img2_small)
    gray = cv2.cvtColor(diff, cv2.COLOR_BGR2GRAY)

    _, thresh = cv2.threshold(gray, 25, 255, cv2.THRESH_BINARY)
    return np.sum(thresh > 0)


# =============================
# UTILS
# =============================

def normalize(arr):
    arr = np.array(arr, dtype=np.float32)
    if np.max(arr) - np.min(arr) == 0:
        return np.zeros_like(arr)
    return (arr - np.min(arr)) / (np.max(arr) - np.min(arr))


def moving_average(arr, k=5):
    arr = np.array(arr, dtype=np.float32)
    if len(arr) < k:
        return arr
    return np.convolve(arr, np.ones(k)/k, mode='same')


def peak_detect(arr, th):
    peaks = []
    for i in range(1, len(arr)-1):
        if arr[i] > arr[i-1] and arr[i] > arr[i+1] and arr[i] > th:
            peaks.append(i)
    return peaks


# =============================
# STEP 1.1 RAW SCORE
# =============================

def compute_raw_scores():
    files = sorted(os.listdir(OUT_DIR))
    total_frames = len(files)

    hist_scores = []
    absdiff_scores = []

    prev = None

    for i in range(total_frames):
        img_path = os.path.join(OUT_DIR, files[i])
        img = cv2.imread(img_path)

        if prev is not None:
            hist_scores.append(hist_diff_score(prev, img))
            absdiff_scores.append(absdiff_score(prev, img))

        prev = img

    return hist_scores, absdiff_scores


# =============================
# STEP 1.2 DETECT CANDIDATE
# =============================

def detect_candidates(hist_scores, absdiff_scores):
    hist_norm = normalize(hist_scores)
    absdiff_norm = normalize(absdiff_scores)

    hist_inv = 1 - hist_norm
    mix = (hist_inv + absdiff_norm) / 2

    smooth = moving_average(mix, k=5)

    mean_val = np.mean(smooth)
    std_val = np.std(smooth)
    th = mean_val + std_val * 1.0

    peaks = peak_detect(smooth, th)

    return peaks, smooth, mix, th


# =============================
# STEP 1.3 YOLO CONFIRM
# =============================

def yolo_compare(model, frame1, frame2):
    r1 = model(frame1)[0]
    r2 = model(frame2)[0]

    c1 = [model.names[int(b.cls)] for b in r1.boxes]
    c2 = [model.names[int(b.cls)] for b in r2.boxes]

    if set(c1) != set(c2):
        return True

    if len(c1) != len(c2):
        return True

    def total_area(r):
        area = 0
        for b in r.boxes.xyxy:
            x1, y1, x2, y2 = b
            area += (x2 - x1) * (y2 - y1)
        return area

    a1 = total_area(r1)
    a2 = total_area(r2)

    ratio = abs(a1 - a2) / (a1 + 1e-5)

    return ratio > 0.20


def confirm_boundaries(peaks, model):
    confirmed = []
    files = sorted(os.listdir(OUT_DIR))

    for p in peaks:
        if p <= 0 or p >= len(files):
            continue

        f0 = cv2.imread(os.path.join(OUT_DIR, files[p-1]))
        f1 = cv2.imread(os.path.join(OUT_DIR, files[p]))

        if yolo_compare(model, f0, f1):
            confirmed.append(p)

    return confirmed


# =============================
# STEP 1.4 PHASE PROCESSING
# =============================

def merge_close_boundaries(indices, min_gap=3):
    if not indices:
        return []

    merged = [indices[0]]

    for x in indices[1:]:
        if x - merged[-1] >= min_gap:
            merged.append(x)

    return merged


def filter_min_phase(indices, total_frames, min_len=25):  # đổi 5 → 25
    result = []

    extended = [0] + indices + [total_frames-1]

    phase_len = np.diff(extended)

    for i in range(1, len(extended)-1):
        if phase_len[i] >= min_len:
            result.append(extended[i])

    return result

def apply_max_phase(indices, total_frames, max_len=150):
    result = []
    extended = [0] + indices + [total_frames-1]

    for i in range(1, len(extended)-1):
        start = extended[i-1]
        end = extended[i]
        length = end - start

        if length > max_len:
            mid = (start + end) // 2
            result.append(mid)
        
        result.append(end)

    return sorted(list(set(result)))


def select_keyframes(indices):
    return indices.copy()

def pick_representative_frames(model, phases, total_frames):
    files = sorted(os.listdir(OUT_DIR))
    reps = []

    extended = [0] + phases + [total_frames-1]

    for i in range(1, len(extended)):
        start = extended[i-1]
        end = extended[i]

        best_frame = start
        best_score = 0

        for f in range(start, end):
            img_path = os.path.join(OUT_DIR, files[f])
            img = cv2.imread(img_path)

            result = model(img)[0]

            score = 0
            for box in result.boxes:
                conf = float(box.conf)
                x1, y1, x2, y2 = box.xyxy[0]
                area = (x2 - x1) * (y2 - y1)
                score += conf * area

            if score > best_score:
                best_score = score
                best_frame = f

        reps.append(best_frame)

    return reps


# =============================
# STEP 2 – OCR (đọc đầu & cuối phase, có fallback)
# =============================

# ocr = PaddleOCR(lang="japan")

# def ocr_text_from_frame(frame_idx):
#     files = sorted(os.listdir(OUT_DIR))
#     img_path = os.path.join(OUT_DIR, files[frame_idx])
#     img = cv2.imread(img_path)

#     result = ocr.predict(img)

#     lines = []
#     for block in result:
#         if "transcription" in block:
#             lines.append(block["transcription"])

#     return lines


# def ocr_with_fallback(start_idx, total_frames, max_attempts=5):
#     """
#     Video replay TikTok: đầu phase có thể nhiễu.
#     → thử tối đa 5 frame liên tiếp cho đến khi có text.
#     """
#     for i in range(max_attempts):
#         idx = start_idx + i
#         if idx >= total_frames:
#             break

#         text = ocr_text_from_frame(idx)
#         if len(text) > 0:
#             return text, idx

#     return [], start_idx  # fallback không có text


# def extract_phase_ocr(keyframes, total_frames):
#     """
#     Với mỗi phase:
#     - đọc frame đầu phase (có fallback)
#     - đọc frame cuối phase (có fallback)
#     """
#     ocr_results = []

#     for i in range(len(keyframes)):
#         start = keyframes[i]
#         if i < len(keyframes) - 1:
#             end = keyframes[i+1] - 1
#         else:
#             end = total_frames - 1

#         text_start, used_start = ocr_with_fallback(start, total_frames)
#         text_end,   used_end   = ocr_with_fallback(end, total_frames)

#         ocr_results.append({
#             "phase_start_frame": start,
#             "phase_end_frame": end,
#             "phase_start_ocr_frame_used": used_start,
#             "phase_end_ocr_frame_used": used_end,
#             "text_start": text_start,
#             "text_end": text_end,
#         })

#     return ocr_results


# =============================
# GPT VISION UTILS
# =============================

def safe_json_load(text):
    if not text:
        return None

    text = text.strip()

    if text.startswith("```"):
        lines = text.splitlines()
        # bỏ dòng ```json
        if lines[0].startswith("```"):
            lines = lines[1:]
        # bỏ dòng ```
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
        text = "\n".join(lines).strip()

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None



def encode_image(path):
    with open(path, "rb") as f:
        return base64.b64encode(f.read()).decode("utf-8")


def gpt_read_header(image_path):
    # print(f"[GPT] reading {os.path.basename(image_path)}")
    img_b64 = encode_image(image_path)

    prompt = """
Phân tích ảnh livestream TikTok và trích xuất CHỈ 2 giá trị sau, dựa 100% vào VỊ TRÍ:

viewer_count:
- Số ở GÓC TRÊN BÊN PHẢI
- Nằm cạnh cụm avatar tròn / biểu tượng người xem
- Không có biểu tượng tim
- Không lấy số xếp hạng, gift, hay số dưới avatar

like_count:
Vị trí: Nằm TRONG khung thông tin của chủ phòng (Profile Card) ở GÓC TRÊN CÙNG BÊN TRÁI.
Tọa độ logic: Con số này nằm ngay bên dưới Tên của chủ phòng.
Định dạng: Thường đi kèm chữ K (nghìn) hoặc M (triệu).
ĐẶC BIỆT LƯU Ý (LOẠI TRỪ):
KHÔNG lấy con số nằm trong các biểu tượng hình "viên thuốc" (pill) hoặc các widget nổi ở GIỮA màn hình (đó là Like Goal/Mục tiêu).
Chỉ lấy số nằm TRONG khối màu trắng mờ chứa ảnh đại diện và tên chủ phòng ở góc trái.

Nếu không thấy đúng vị trí → trả null.

Chỉ trả JSON:
{"viewer_count": number | null, "like_count": number | null}
""".strip()

    resp = client.responses.create(
        model=GPT5_MODEL,
        input=[{
            "role": "user",
            "content": [
                {"type": "input_text", "text": prompt},
                {
                    "type": "input_image",
                    "image_url": f"data:image/jpeg;base64,{img_b64}"
                }
            ]
        }],
        max_output_tokens=1024
    )

    return safe_json_load(resp.output_text)


# =============================
# PHASE FALLBACK LOGIC
# =============================

def read_phase_start(files, frame_idx):
    for i in range(MAX_FALLBACK):
        idx = frame_idx + i
        if idx >= len(files):
            break

        path = os.path.join(OUT_DIR, files[idx])
        data = gpt_read_header(path)

        if data and data.get("viewer_count") is not None:
            return data, idx

    return None, frame_idx


def read_phase_end(files, frame_idx):
    for i in range(MAX_FALLBACK):
        idx = frame_idx - i
        if idx < 0:
            break

        path = os.path.join(OUT_DIR, files[idx])
        data = gpt_read_header(path)

        if data and data.get("viewer_count") is not None:
            return data, idx

    return None, frame_idx


# =============================
# PHASE STAT EXTRACTION
# =============================

def extract_phase_stats(keyframes, total_frames):
    files = sorted(os.listdir(OUT_DIR))
    results = []

    extended = [0] + keyframes + [total_frames]

    for i in range(len(extended) - 1):
        start = extended[i]
        end = extended[i + 1] - 1

        # print(f"\n[PHASE {i+1}] {start} → {end}")

        start_data, start_used = read_phase_start(files, start)
        end_data, end_used = read_phase_end(files, end)

        # print(f"  start_frame_used: {start_used}")
        # print(f"  start_stats: {start_data}")
        # print(f"  end_frame_used:   {end_used}")
        # print(f"  end_stats:   {end_data}")

        results.append({
            "phase_index": i + 1,
            "phase_start_frame": start,
            "phase_start_used_frame": start_used,
            "start": start_data,
            "phase_end_frame": end,
            "phase_end_used_frame": end_used,
            "end": end_data
        })

    return results


# =============================
# PHASE AUDIO TO TEXT STEP 3
# =============================
def extract_audio_chunks():
    video_name = os.path.splitext(os.path.basename(VIDEO_PATH))[0]
    out_dir = os.path.join(AUDIO_OUT_ROOT, video_name)
    os.makedirs(out_dir, exist_ok=True)

    chunk_pattern = os.path.join(out_dir, "chunk_%03d.wav")

    subprocess.run(
        [
            FFMPEG_BIN, "-y",
            "-i", VIDEO_PATH,
            "-f", "segment",
            "-segment_time", str(CHUNK_SECONDS),
            "-ar", "16000",
            "-ac", "1",
            chunk_pattern
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL
    )

    return out_dir

def transcribe_audio_chunks(audio_dir: str):
    video_name = os.path.basename(audio_dir)
    text_dir = os.path.join(AUDIO_TEXT_ROOT, video_name)
    os.makedirs(text_dir, exist_ok=True)

    url = WHISPER_ENDPOINT

    files = sorted([
        f for f in os.listdir(audio_dir)
        if f.startswith("chunk_") and f.endswith(".wav")
    ])

    for f in files:
        wav_path = os.path.join(audio_dir, f)
        txt_path = os.path.join(text_dir, f.replace(".wav", ".txt"))

        print(f"[AZURE Whisper] {wav_path}")

        # ---- RETRY LOOP ----
        for attempt in range(1, MAX_RETRY + 1):

            with open(wav_path, "rb") as audio_file:
                audio_data = audio_file.read()

            response = requests.post(
                url,
                headers={"api-key": AZURE_KEY},
                files={
                    "file": (f, audio_data, "audio/wav"),
                    "response_format": (None, "verbose_json"),  # Bắt buộc để có language và chi tiết timestamp
                    "timestamp_granularities[]": (None, "word"), # Lấy timestamp từng từ (hoặc "segment" cho từng đoạn)
                    "timestamp_granularities[]": (None, "segment"), # Có thể thêm cả hai
                    "temperature": (None, "0"),
                    "task": (None, "transcribe"),
                    "language": (None, "ja"), # Chỉ định tiếng Nhật
                }
            )

            # Kết quả trả về sẽ là JSON
            result = response.json()
            print(f"Ngôn ngữ: {result.get('language')}")
            print(f"Timestamp từng từ: {result.get('words')}")


            if response.status_code == 200:
                data = response.json()

                chunk_index = int(f.split("_")[1].split(".")[0])   # lấy số 000, 001, 002...
                offset = chunk_index * CHUNK_SECONDS

                with open(txt_path, "w", encoding="utf-8") as out:
                    out.write("[TEXT]\n")
                    out.write(data.get("text", ""))

                    out.write("\n\n[TIMELINE]\n")
                    for seg in data.get("segments", []):
                        start = seg['start'] + offset
                        end = seg['end'] + offset
                        out.write(f"{start:.2f}s → {end:.2f}s : {seg['text']}\n")

                print(f"[OK] Saved → {txt_path}")
                break

            # Nếu quá quota — sleep rồi retry
            if response.status_code == 429:
                wait_time = 5 * attempt + random.uniform(1, 3)
                print(f"[WAIT] 429 rate limit → retry #{attempt} after {wait_time:.1f}s")
                time.sleep(wait_time)
                continue

            # Nếu lỗi khác → báo và bỏ qua chunk
            print("ERROR:", response.text)
            break

        # ---- THROTTLE CONTROL BETWEEN REQUESTS ----
        print(f"[SLEEP] {SLEEP_BETWEEN_REQUESTS}s to avoid quota")
        time.sleep(SLEEP_BETWEEN_REQUESTS)


# =============================
# STEP 4. Representative frames to TEXT
# =============================
def gpt_image_caption(image_path):
    img_b64 = encode_image(image_path)

    prompt = """
Ảnh này là một key frame đại diện cho MỘT PHASE trong livestream bán hàng.

Hãy mô tả trạng thái trực quan của phase này,
theo cách phù hợp để SO SÁNH và NHÓM các phase giống nhau.

YÊU CẦU:
- Chỉ mô tả những gì NHÌN THẤY
- Tập trung vào:
  + Sản phẩm có đang được trình bày / cầm / demo hay không
  + Mức độ tập trung của camera vào sản phẩm
  + Trạng thái chung của khung hình
- KHÔNG suy đoán cảm xúc, ý định, hiệu quả
- KHÔNG nhắc tới thời gian, số liệu, hay người xem

Chỉ trả JSON:
{
  "visual_phase_description": "string"
}
""".strip()

    for attempt in range(1, MAX_RETRY + 1):
        resp = client.responses.create(
            model=GPT5_MODEL,
            input=[{
                "role": "user",
                "content": [
                    {"type": "input_text", "text": prompt},
                    {
                        "type": "input_image",
                        "image_url": f"data:image/jpeg;base64,{img_b64}"
                    }
                ]
            }],
            max_output_tokens=1024
        )

        raw = resp.output_text

        # print("\n========== GPT RAW OUTPUT ==========")
        # print(f"LEN = {len(raw) if raw else 0}")
        # print(raw)
        # print("====================================\n")


        data = safe_json_load(raw)

        if data:
            return data

        wait = 2 * attempt + random.uniform(0.5, 1.5)
        print(f"[RETRY] caption retry #{attempt}, sleep {wait:.1f}s")
        time.sleep(wait)

    return None


def caption_keyframes(rep_frames):
    files = sorted(os.listdir(OUT_DIR))
    results = []

    for idx in rep_frames:
        img_path = os.path.join(OUT_DIR, files[idx])

        caption_data = gpt_image_caption(img_path)

        results.append({
            "frame_index": idx,
            "image": files[idx],
            "caption": caption_data.get("visual_phase_description") if caption_data else None
        })

        time.sleep(3)

    return results



# =============================
# STEP 5. Merge Phase
# =============================
def load_all_audio_segments(audio_text_dir):
    segments = []

    for f in sorted(os.listdir(audio_text_dir)):
        if not f.endswith(".txt"):
            continue

        with open(os.path.join(audio_text_dir, f), "r", encoding="utf-8") as fin:
            lines = fin.readlines()

        in_timeline = False
        for line in lines:
            line = line.strip()
            if line == "[TIMELINE]":
                in_timeline = True
                continue

            if not in_timeline or "→" not in line:
                continue

            try:
                time_part, text = line.split(":", 1)
                start_s, end_s = time_part.replace("s", "").split("→")
                segments.append({
                    "start": float(start_s.strip()),
                    "end": float(end_s.strip()),
                    "text": text.strip()
                })
            except Exception:
                continue

    return segments


def collect_speech_for_phase(segments, start_sec, end_sec):
    texts = []
    for seg in segments:
        if seg["end"] < start_sec:
            continue
        if seg["start"] > end_sec:
            break
        texts.append(seg["text"])
    return " ".join(texts)


def build_phase_units(
    keyframes,
    rep_frames,
    keyframe_captions,
    phase_stats,
    total_frames
):
    audio_segments = load_all_audio_segments(
        os.path.join(AUDIO_TEXT_ROOT, os.path.splitext(os.path.basename(VIDEO_PATH))[0])
    )

    files = sorted(os.listdir(OUT_DIR))
    phase_units = []

    for i, ps in enumerate(phase_stats):
        start_sec = ps["phase_start_frame"]
        end_sec   = ps["phase_end_frame"]

        speech_text = collect_speech_for_phase(
            audio_segments, start_sec, end_sec
        )

        rep_idx = rep_frames[i]
        caption = keyframe_captions[i]["caption"]

        phase_units.append({
            "phase_index": i + 1,
            "time_range": {
                "start_sec": start_sec,
                "end_sec": end_sec
            },
            "key_frame": {
                "frame_index": rep_idx,
                "image": files[rep_idx]
            },
            "image_caption": caption,
            "speech_text": speech_text,
            "metric_timeseries": {

                "start": ps["start"],
                "end": ps["end"],
                "start_used_frame": ps["phase_start_used_frame"],
                "end_used_frame": ps["phase_end_used_frame"]
            }
        })

    return phase_units


def save_phase_units_to_json(phase_units):
    video_name = os.path.splitext(os.path.basename(VIDEO_PATH))[0]
    out_dir = os.path.join("phase", video_name)
    os.makedirs(out_dir, exist_ok=True)

    out_path = os.path.join(out_dir, "phase_units.json")
    dump_phase_units_pretty_embedding_1line(phase_units, out_path)

    print(f"[OK] Phase units saved → {out_path}")



# =============================
# STEP 6. Build Phase Description
# =============================

SYSTEM_PROMPT_PHASE_DESC  = """
Bạn là một hệ thống phân tích livestream bán hàng.

Bạn sẽ nhận dữ liệu của MỘT phase, gồm HAI phần trong user input:
1) IMAGE CAPTION:
   - Là mô tả hình ảnh đại diện của phase
   - Chỉ phản ánh trạng thái trực quan (có/không có trình bày sản phẩm, mức độ cận cảnh, v.v.)

2) SPEECH TEXT:
   - Là nội dung lời nói của người dẫn trong phase đó
   - Phản ánh hành vi, mục đích và vai trò của phase trong livestream

Nhiệm vụ của bạn:
Tạo một PHASE DESCRIPTION nhằm phục vụ việc SO SÁNH và NHÓM các phase giống nhau.

YÊU CẦU:
- Viết 4–6 câu
- Mô tả hành vi chính của người dẫn trong phase
- Mô tả trạng thái trình bày sản phẩm (nếu có, không suy đoán)
- Cho biết vai trò của lời nói (giải thích, demo, kêu gọi, nói chuyện, filler, chuyển tiếp)
- Không nhắc tên sản phẩm cụ thể nếu hình ảnh không cho thấy rõ
- Không nhắc giá, số liệu, thời gian, viewer, like
- Không đưa ra nhận xét hay đánh giá

Mục tiêu là để các phase có hành vi và cách trình bày tương tự
sẽ có mô tả tương tự về mặt ngữ nghĩa.

Output JSON:
{
  "phase_description": "string"
}
""".strip()

SYSTEM_PROMPT_PHASE_DESC_JA = """
あなたはライブコマース配信を分析するシステムです。

あなたは1つのフェーズについて、以下の情報を受け取ります。

1) IMAGE CAPTION:
   - フェーズを代表する画像の視覚的な説明
   - 見た目の状態のみを反映する

2) SPEECH TEXT:
   - そのフェーズ中の配信者の発話内容
   - 行動や役割を反映する

タスク：
フェーズ同士を比較・分類するための
PHASE DESCRIPTIONを作成してください。

要件：
- 4〜6文
- 配信者の主な行動を説明する
- 商品を提示・説明・デモしているかを記述する（推測しない）
- 話し方の役割（説明、デモ、呼びかけ、雑談など）を示す
- 商品名、価格、数値、時間、視聴者数は書かない
- 評価や感想は書かない

出力（JSON）：
{
  "phase_description": "string"
}
""".strip()

SYSTEM_PROMPT_PHASE_DESC_ZHTW = """
你是一個直播帶貨分析系統。

你將收到一個Phase的資料，包含以下兩部分：
1) IMAGE CAPTION:
   - Phase代表畫面的視覺描述
   - 僅反映視覺狀態（是否展示商品、特寫程度等）

2) SPEECH TEXT:
   - 該Phase中主播的發言內容
   - 反映行為、目的和在直播中的角色

任務：
建立一個PHASE DESCRIPTION，用於比較和分類相似的Phase。

要求：
- 寫4到6句
- 描述主播的主要行為
- 描述商品展示狀態（如有，不要推測）
- 說明發言的角色（說明、展示、呼籲、閒聊、過場等）
- 如果畫面看不清楚，不要提及具體商品名稱
- 不要提及價格、數據、時間、觀眾數
- 不要做出評價或感想

輸出（JSON）：
{
  "phase_description": "string"
}
""".strip()




# def build_phase_descriptions(phase_units):
#     results = []

#     for p in phase_units:
#         user_input = f"""
# IMAGE CAPTION:
# {p['image_caption']}

# SPEECH TEXT:
# {p['speech_text']}
# """.strip()

#         resp = client.responses.create(
#             model=GPT5_MODEL,
#             input=[
#                 {
#                     "role": "system",
#                     "content": [
#                         {"type": "input_text", "text": SYSTEM_PROMPT_PHASE_DESC}
#                     ]
#                 },
#                 {
#                     "role": "user",
#                     "content": [
#                         {"type": "input_text", "text": user_input}
#                     ]
#                 }
#             ],
#             max_output_tokens=2048
#         )

#         raw = resp.output_text.strip()
#         data = safe_json_load(raw)

#         results.append({
#             **p,
#             "phase_description": (
#                 data.get("phase_description") if data else raw
#             )
#         })

#     return results


def build_phase_descriptions(phase_units):
    results = []

    for p in phase_units:
        user_input = f"""
IMAGE CAPTION:
{p['image_caption']}

SPEECH TEXT:
{p['speech_text']}
""".strip()

        phase_desc = None

        try:
            resp = client.responses.create(
                model=GPT5_MODEL,
                input=[
                    {
                        "role": "system",
                        "content": [
                            {"type": "input_text", "text": SYSTEM_PROMPT_PHASE_DESC}
                        ]
                    },
                    {
                        "role": "user",
                        "content": [
                            {"type": "input_text", "text": user_input}
                        ]
                    }
                ],
                max_output_tokens=2048
            )

            raw = resp.output_text.strip()
            data = safe_json_load(raw)
            phase_desc = data.get("phase_description") if data else raw

        except Exception as e:
            print(f"[WARN][STEP 6] phase {p['phase_index']} blocked by content filter")

        # ===== HARD FALLBACK (BẮT BUỘC) =====
        if not phase_desc:
            phase_desc = (
                "Phase này bao gồm hoạt động trình bày và giao tiếp trong livestream. "
                "Người dẫn tương tác với người xem và cung cấp thông tin liên quan đến nội dung đang được hiển thị. "
                "Mô tả chi tiết không khả dụng do giới hạn xử lý nội dung."
            )

        results.append({
            **p,
            "phase_description": phase_desc
        })

    return results

def rebuild_phase_descriptions_ja(phase_units):
    results = []

    for p in phase_units:
        user_input = f"""
IMAGE CAPTION:
{p.get("image_caption")}

SPEECH TEXT:
{p.get("speech_text")}
""".strip()

        desc = None

        try:
            resp = client.responses.create(
                model=GPT5_MODEL,
                input=[
                    {
                        "role": "system",
                        "content": [
                            {"type": "input_text", "text": SYSTEM_PROMPT_PHASE_DESC_JA}
                        ]
                    },
                    {
                        "role": "user",
                        "content": [
                            {"type": "input_text", "text": user_input}
                        ]
                    }
                ],
                max_output_tokens=2048
            )

            raw = resp.output_text
            data = safe_json_load(raw)
            desc = data.get("phase_description") if data else raw

        except Exception:
            print(f"[WARN][JP DESC] phase {p['phase_index']} blocked")

        # fallback cứng (không để pipeline chết)
        if not desc:
            desc = (
                "このフェーズでは、配信者が視聴者とやり取りしながら、"
                "画面に表示されている内容に関連する説明や進行を行っている。"
                "詳細な説明は処理制限により取得できなかった。"
            )

        results.append({
            **p,
            "phase_description_ja": desc
        })

    return results


def rebuild_phase_descriptions_zhtw(phase_units):
    results = []

    for p in phase_units:
        user_input = f"""
IMAGE CAPTION:
{p.get("image_caption")}

SPEECH TEXT:
{p.get("speech_text")}
""".strip()

        desc = None

        try:
            resp = client.responses.create(
                model=GPT5_MODEL,
                input=[
                    {
                        "role": "system",
                        "content": [
                            {"type": "input_text", "text": SYSTEM_PROMPT_PHASE_DESC_ZHTW}
                        ]
                    },
                    {
                        "role": "user",
                        "content": [
                            {"type": "input_text", "text": user_input}
                        ]
                    }
                ],
                max_output_tokens=2048
            )

            raw = resp.output_text
            data = safe_json_load(raw)
            desc = data.get("phase_description") if data else raw

        except Exception:
            print(f"[WARN][ZHTW DESC] phase {p['phase_index']} blocked")

        if not desc:
            desc = (
                "此Phase中，主播與觀眾互動並提供與畫面內容相關的說明。"
                "由於內容處理限制，無法取得詳細描述。"
            )

        results.append({
            **p,
            "phase_description_zhtw": desc
        })

    return results


# =============================
# STEP 7 – GLOBAL PHASE GROUPING
# =============================
def l2_normalize(v):
    v = np.array(v, dtype=np.float32)
    norm = np.linalg.norm(v)
    if norm == 0:
        return v
    return v / norm


def cosine(a, b):
    # assume both normalized
    return float(np.dot(a, b))

def embed_phase_descriptions(phase_units):
    texts = [p["phase_description"] for p in phase_units]

    resp = embed_client.embeddings.create(
        model=EMBED_MODEL,
        input=texts
    )

    for p, e in zip(phase_units, resp.data):
        p["embedding"] = l2_normalize(e.embedding).tolist()

    return phase_units

def load_global_groups():
    os.makedirs(GROUP_ROOT, exist_ok=True)
    path = os.path.join(GROUP_ROOT, GROUP_FILE)

    if not os.path.exists(path):
        return []

    with open(path, "r", encoding="utf-8") as f:
        raw = json.load(f)

    groups = []
    for g in raw:
        groups.append({
            "group_id": g["group_id"],
            "centroid": np.array(g["centroid"], dtype=np.float32),
            "size": g["size"]
        })

    return groups

def assign_phases_to_groups(phase_units, groups, embedding_key="embedding"):
    for p in phase_units:
        v = np.array(p[embedding_key], dtype=np.float32)

        best_group = None
        best_score = -1

        for g in groups:
            score = cosine(v, g["centroid"])
            if score > best_score:
                best_score = score
                best_group = g

        # CASE 1: JOIN EXISTING GROUP
        if best_group and best_score >= COSINE_THRESHOLD:
            n = best_group["size"]
            new_centroid = (best_group["centroid"] * n + v) / (n + 1)
            best_group["centroid"] = l2_normalize(new_centroid)
            best_group["size"] += 1

            p["group_id"] = best_group["group_id"]

        # CASE 2: CREATE NEW GROUP
        else:
            new_id = len(groups) + 1
            groups.append({
                "group_id": new_id,
                "centroid": v,
                "size": 1
            })
            p["group_id"] = new_id

    return phase_units, groups


# def save_global_groups(groups):
#     path = os.path.join(GROUP_ROOT, GROUP_FILE)

#     serializable = []
#     for g in groups:
#         serializable.append({
#             "group_id": g["group_id"],
#             "size": g["size"],
#             "centroid": g["centroid"].tolist()
#         })

#     with open(path, "w", encoding="utf-8") as f:
#         # json.dump(serializable, f, ensure_ascii=False)
#         dump_groups_pretty_embedding_1line(groups, GROUP_FILE)

#     print(f"[OK] Global groups saved → {path}")

def save_global_groups(groups):
    path = os.path.join(GROUP_ROOT, GROUP_FILE)
    dump_groups_pretty_embedding_1line(groups, path)
    print(f"[OK] Global groups saved → {path}")


def dump_groups_pretty_embedding_1line(groups, path):
    with open(path, "w", encoding="utf-8") as f:
        f.write("[\n")
        for i, g in enumerate(groups):
            centroid = ",".join(f"{x:.6f}" for x in g["centroid"])

            f.write("  {\n")
            f.write(f'    "group_id": {g["group_id"]},\n')
            f.write(f'    "size": {g["size"]},\n')
            f.write(f'    "centroid": [{centroid}]\n')
            f.write("  }")

            if i < len(groups) - 1:
                f.write(",\n")
        f.write("\n]")

def dump_phase_units_pretty_embedding_1line(phase_units, path):
    with open(path, "w", encoding="utf-8") as f:
        f.write("[\n")
        for i, p in enumerate(phase_units):
            f.write("  {\n")

            keys = list(p.keys())
            for j, k in enumerate(keys):
                v = p[k]

                if k == "embedding" and isinstance(v, list):
                    emb = ",".join(f"{x:.6f}" for x in v)
                    f.write(f'    "embedding": [{emb}]')
                else:
                    dumped = json.dumps(v, ensure_ascii=False, indent=4)
                    dumped = dumped.replace("\n", "\n    ")
                    f.write(f'    "{k}": {dumped}')

                if j < len(keys) - 1:
                    f.write(",\n")
                else:
                    f.write("\n")

            f.write("  }")
            if i < len(phase_units) - 1:
                f.write(",\n")
        f.write("\n]")

GROUP_FILE_JA = "groups_ja.json"
BEST_PHASE_FILE_JA = os.path.join(GROUP_ROOT, "group_best_phases_ja.json")

# ── ZH-TW (Traditional Chinese) constants ──
GROUP_FILE_ZHTW = "groups_zhtw.json"
BEST_PHASE_FILE_ZHTW = os.path.join(GROUP_ROOT, "group_best_phases_zhtw.json")


def embed_phase_descriptions_ja(phase_units):
    texts = [p["phase_description_ja"] for p in phase_units]

    resp = embed_client.embeddings.create(
        model=EMBED_MODEL,
        input=texts
    )

    for p, e in zip(phase_units, resp.data):
        p["embedding_ja"] = l2_normalize(e.embedding).tolist()

    return phase_units

def load_global_groups_ja():
    path = os.path.join(GROUP_ROOT, GROUP_FILE_JA)
    if not os.path.exists(path):
        return []

    with open(path, "r", encoding="utf-8") as f:
        raw = json.load(f)

    return [
        {
            "group_id": g["group_id"],
            "centroid": np.array(g["centroid"], dtype=np.float32),
            "size": g["size"]
        }
        for g in raw
    ]

def save_global_groups_ja(groups):
    path = os.path.join(GROUP_ROOT, GROUP_FILE_JA)
    dump_groups_pretty_embedding_1line(groups, path)
    print(f"[OK] JP groups saved → {path}")


def embed_phase_descriptions_zhtw(phase_units):
    texts = [p["phase_description_zhtw"] for p in phase_units]

    resp = embed_client.embeddings.create(
        model=EMBED_MODEL,
        input=texts
    )

    for p, e in zip(phase_units, resp.data):
        p["embedding_zhtw"] = l2_normalize(e.embedding).tolist()

    return phase_units

def load_global_groups_zhtw():
    path = os.path.join(GROUP_ROOT, GROUP_FILE_ZHTW)
    if not os.path.exists(path):
        return []

    with open(path, "r", encoding="utf-8") as f:
        raw = json.load(f)

    return [
        {
            "group_id": g["group_id"],
            "centroid": np.array(g["centroid"], dtype=np.float32),
            "size": g["size"]
        }
        for g in raw
    ]

def save_global_groups_zhtw(groups):
    path = os.path.join(GROUP_ROOT, GROUP_FILE_ZHTW)
    dump_groups_pretty_embedding_1line(groups, path)
    print(f"[OK] ZH-TW groups saved -> {path}")



# =============================
# STEP 8 – GROUP BEST PHASES
# =============================

def extract_attention_metrics(phase):
    start = phase["metric_timeseries"].get("start") or {}
    end   = phase["metric_timeseries"].get("end") or {}

    start_view = start.get("viewer_count")
    end_view   = end.get("viewer_count")

    start_like = start.get("like_count")
    end_like   = end.get("like_count")

    duration = (
        phase["time_range"]["end_sec"]
        - phase["time_range"]["start_sec"]
    )

    delta_view = (
        end_view - start_view
        if start_view is not None and end_view is not None
        else None
    )

    delta_like = (
        end_like - start_like
        if start_like is not None and end_like is not None
        else None
    )

    view_velocity = (
        delta_view / duration
        if delta_view is not None and duration > 0
        else None
    )

    like_velocity = (
        delta_like / duration
        if delta_like is not None and duration > 0
        else None
    )

    avg_view = (
        (start_view + end_view) / 2
        if start_view is not None and end_view is not None
        else None
    )

    like_per_viewer = (
        delta_like / avg_view
        if delta_like is not None and avg_view and avg_view > 0
        else None
    )

    return {
        "delta_view": delta_view,
        "view_velocity": view_velocity,
        "delta_like": delta_like,
        "like_velocity": like_velocity,
        "like_per_viewer": like_per_viewer
    }

def compute_attention_score(m):
    score = 0.0

    if m["view_velocity"] is not None:
        score += 0.4 * m["view_velocity"]

    if m["like_velocity"] is not None:
        score += 0.3 * m["like_velocity"]

    if m["like_per_viewer"] is not None:
        score += 0.2 * m["like_per_viewer"]

    if m["delta_view"] is not None:
        score += 0.1 * m["delta_view"]

    return score

def load_group_best_phases():
    if not os.path.exists(BEST_PHASE_FILE):
        return {
            "version": "v1_attention",
            "groups": {}
        }

    with open(BEST_PHASE_FILE, "r", encoding="utf-8") as f:
        return json.load(f)

def update_group_best_phases(phase_units, best_data, video_id):
    for p in phase_units:
        group_id = str(p.get("group_id"))
        if not group_id:
            continue

        metrics = extract_attention_metrics(p)
        score = compute_attention_score(metrics)

        phase_entry = {
            "phase_id": f"{video_id}_phase_{p['phase_index']}",
            "video_id": video_id,
            "phase_index": p["phase_index"],
            "score": score,
            "metrics": metrics
        }

        group = best_data["groups"].setdefault(
            group_id,
            {"top_k": TOP_K, "phases": []}
        )

        group["phases"].append(phase_entry)

        # sort giảm dần theo score
        group["phases"].sort(key=lambda x: x["score"], reverse=True)

        # giữ top K
        group["phases"] = group["phases"][:TOP_K]

    return best_data

def save_group_best_phases(best_data):
    with open(BEST_PHASE_FILE, "w", encoding="utf-8") as f:
        json.dump(best_data, f, ensure_ascii=False, indent=2)

    print(f"[OK] Best phases saved → {BEST_PHASE_FILE}")

def load_group_best_phases_ja():
    if not os.path.exists(BEST_PHASE_FILE_JA):
        return {"version": "v1_attention", "groups": {}}

    with open(BEST_PHASE_FILE_JA, "r", encoding="utf-8") as f:
        return json.load(f)

def save_group_best_phases_ja(best_data):
    with open(BEST_PHASE_FILE_JA, "w", encoding="utf-8") as f:
        json.dump(best_data, f, ensure_ascii=False, indent=2)

    print(f"[OK] JP best phases saved → {BEST_PHASE_FILE_JA}")


def load_group_best_phases_zhtw():
    if not os.path.exists(BEST_PHASE_FILE_ZHTW):
        return {"version": "v1_attention", "groups": {}}

    with open(BEST_PHASE_FILE_ZHTW, "r", encoding="utf-8") as f:
        return json.load(f)

def save_group_best_phases_zhtw(best_data):
    with open(BEST_PHASE_FILE_ZHTW, "w", encoding="utf-8") as f:
        json.dump(best_data, f, ensure_ascii=False, indent=2)

    print(f"[OK] ZH-TW best phases saved -> {BEST_PHASE_FILE_ZHTW}")




# =============================
# STEP 9 – BUILD REPORTS + GPT REWRITE
# =============================
def build_report_1_timeline(phase_units):
    out = []

    for p in phase_units:
        start = p["metric_timeseries"]["start"]
        end   = p["metric_timeseries"]["end"]

        out.append({
            "phase_index": p["phase_index"],
            "group_id": p.get("group_id"),
            "phase_description": p["phase_description"],
            "time_range": p["time_range"],
            "metrics": {
                "view_start": start.get("viewer_count"),
                "view_end": end.get("viewer_count"),
                "like_start": start.get("like_count"),
                "like_end": end.get("like_count"),
                "delta_view": (
                    end.get("viewer_count") - start.get("viewer_count")
                    if start.get("viewer_count") is not None
                       and end.get("viewer_count") is not None
                    else None
                ),
                "delta_like": (
                    end.get("like_count") - start.get("like_count")
                    if start.get("like_count") is not None
                       and end.get("like_count") is not None
                    else None
                )
            }
        })

    return out

def build_report_2_phase_insights_raw(phase_units, best_data):
    out = []

    for p in phase_units:
        gid = str(p.get("group_id"))
        if not gid:
            continue

        best_group = best_data["groups"].get(gid)
        if not best_group or not best_group["phases"]:
            continue

        best = best_group["phases"][0]

        cur = extract_attention_metrics(p)
        ref = best["metrics"]

        findings = []

        if cur["view_velocity"] is not None and ref["view_velocity"] is not None:
            if cur["view_velocity"] < ref["view_velocity"]:
                findings.append("view_velocity_lower_than_best")

        if cur["like_per_viewer"] is not None and ref["like_per_viewer"] is not None:
            if cur["like_per_viewer"] < ref["like_per_viewer"]:
                findings.append("like_per_viewer_lower_than_best")

        out.append({
            "phase_index": p["phase_index"],
            "group_id": gid,
            "phase_description": p["phase_description"],
            "current_metrics": cur,
            "benchmark_metrics": ref,
            "findings": findings
        })

    return out

PROMPT_REPORT_2 = """
You are analyzing a livestream phase.

You are given:
- The phase description
- Metric comparison results against the best historical phase of the same type

Rules:
- Do NOT calculate metrics
- Do NOT invent data
- Only explain what can be improved
- Be concrete and actionable

Write 2–4 bullet points.

Input:
{data}
"""


# def rewrite_report_2_with_gpt(raw_items):
#     out = []

#     for item in raw_items:
#         payload = json.dumps(item, ensure_ascii=False)

#         resp = client.responses.create(
#             model=GPT5_MODEL,
#             input=[
#                 {
#                     "role": "system",
#                     "content": [
#                         {"type": "input_text", "text": "You rewrite technical findings into practical advice."}
#                     ]
#                 },
#                 {
#                     "role": "user",
#                     "content": [
#                         {"type": "input_text", "text": PROMPT_REPORT_2.format(data=payload)}
#                     ]
#                 }
#             ],
#             max_output_tokens=2048
#         )

#         out.append({
#             "phase_index": item["phase_index"],
#             "group_id": item["group_id"],
#             "insight": resp.output_text.strip()
#         })

#     return out

def is_gpt_report_2_invalid(text: str) -> bool:
    if not text:
        return True

    t = text.strip().lower()

    refusal_signals = [
        "i’m sorry",
        "i am sorry",
        "i cannot assist",
        "i can’t assist",
        "cannot help",
        "not able to help",
        "cannot comply"
    ]

    return any(sig in t for sig in refusal_signals)


def rewrite_report_2_with_gpt(raw_items):
    out = []

    for item in raw_items:
        payload = json.dumps(item, ensure_ascii=False)

        insight_text = None

        for attempt in range(1, MAX_RETRY + 1):
            prompt = (
                PROMPT_REPORT_2.format(data=payload)
                if attempt == 1
                else PROMPT_REPORT_2.format(data=payload)
            )

            resp = client.responses.create(
                model=GPT5_MODEL,
                input=[
                    {
                        "role": "system",
                        "content": [
                            {"type": "input_text", "text": "Bạn là chuyên gia phân tích livestream bán hàng."}
                        ]
                    },
                    {
                        "role": "user",
                        "content": [
                            {"type": "input_text", "text": prompt}
                        ]
                    }
                ],
                max_output_tokens=2048
            )

            text = resp.output_text.strip() if resp.output_text else ""

            if not is_gpt_report_2_invalid(text):
                insight_text = text
                break

            # retry backoff nhẹ
            wait = 2 * attempt + random.uniform(0.5, 1.5)
            print(f"[RETRY][Report2] phase {item['phase_index']} attempt {attempt} failed → sleep {wait:.1f}s")
            time.sleep(wait)

        # fallback sản phẩm (KHÔNG lộ lỗi GPT)
        if not insight_text:
            insight_text = (
                "Phase này hiện chưa có đủ tín hiệu nổi bật để đưa ra khuyến nghị cụ thể. "
                "Khi có thêm dữ liệu từ các livestream khác, hệ thống sẽ cung cấp gợi ý chi tiết hơn."
            )

        out.append({
            "phase_index": item["phase_index"],
            "group_id": item["group_id"],
            "insight": insight_text
        })

    return out


def build_report_3_video_insights_raw(phase_units):
    groups = {}

    for p in phase_units:
        gid = str(p.get("group_id"))
        if not gid:
            continue

        m = extract_attention_metrics(p)

        g = groups.setdefault(gid, {
            "phase_count": 0,
            "total_delta_view": 0,
            "total_delta_like": 0
        })

        g["phase_count"] += 1
        if m["delta_view"] is not None:
            g["total_delta_view"] += m["delta_view"]
        if m["delta_like"] is not None:
            g["total_delta_like"] += m["delta_like"]

    return {
        "total_phases": len(phase_units),
        "group_performance": [
            {
                "group_id": gid,
                "phase_count": g["phase_count"],
                "total_delta_view": g["total_delta_view"],
                "total_delta_like": g["total_delta_like"]
            }
            for gid, g in groups.items()
        ]
    }


# PROMPT_REPORT_3 = """
# You are evaluating the overall structure and performance of a livestream video.

# You are given:
# - Aggregated performance per phase group
# - No raw time-series

# Your task:
# - Identify strengths and weaknesses in the video structure
# - Explain which phase types help or hurt performance
# - Suggest high-level improvements to pacing or composition

# Rules:
# - Do NOT invent metrics
# - Do NOT mention group IDs explicitly unless necessary
# - Output 3–5 concise insight bullets

# Input:
# {data}
# """

PROMPT_REPORT_3 = """
Bạn đang phân tích HIỆU QUẢ TỔNG THỂ của một video livestream bán hàng.

Bạn được cung cấp:
- Dữ liệu tổng hợp hiệu quả theo từng nhóm phase
- KHÔNG có dữ liệu time-series chi tiết

NHIỆM VỤ:
- Phân tích cấu trúc video
- Chỉ ra điểm mạnh, điểm yếu
- Đưa ra gợi ý cải thiện ở mức tổng thể

YÊU CẦU BẮT BUỘC:
- Viết bằng TIẾNG VIỆT
- KHÔNG nhắc đến group_id
- KHÔNG bịa số liệu
- Mỗi insight là MỘT object riêng

FORMAT OUTPUT JSON (BẮT BUỘC):
{
  "video_insights": [
    {
      "title": "string ngắn gọn",
      "content": "mô tả insight vài câu"
    }
  ]
}

INPUT DATA:
{data}
"""



# def rewrite_report_3_with_gpt(raw_video_insight):
#     payload = json.dumps(raw_video_insight, ensure_ascii=False)

#     resp = client.responses.create(
#         model=GPT5_MODEL,
#         input=[
#             {
#                 "role": "system",
#                 "content": [
#                     {"type": "input_text", "text": "You write high-level analytical insights."}
#                 ]
#             },
#             {
#                 "role": "user",
#                 "content": [
#                     {"type": "input_text", "text": PROMPT_REPORT_3.format(data=payload)}
#                 ]
#             }
#         ],
#         max_output_tokens=2048
#     )

#     return {
#         "video_insights": resp.output_text.strip()
#     }

def rewrite_report_3_with_gpt(raw_video_insight):
    payload = json.dumps(raw_video_insight, ensure_ascii=False)

    prompt = f"""
Bạn đang phân tích HIỆU QUẢ TỔNG THỂ của một video livestream bán hàng.

Bạn được cung cấp:
- Dữ liệu tổng hợp hiệu quả theo từng nhóm phase
- KHÔNG có dữ liệu time-series chi tiết

NHIỆM VỤ:
- Phân tích cấu trúc video
- Chỉ ra điểm mạnh, điểm yếu
- Đưa ra gợi ý cải thiện ở mức tổng thể

YÊU CẦU BẮT BUỘC:
- Viết bằng TIẾNG VIỆT
- KHÔNG nhắc đến group_id
- KHÔNG bịa số liệu
- Mỗi insight là MỘT object riêng

FORMAT OUTPUT JSON (BẮT BUỘC):
{{
  "video_insights": [
    {{
      "title": "string ngắn gọn",
      "content": "mô tả insight vài câu"
    }}
  ]
}}

INPUT DATA:
{payload}
"""

    resp = client.responses.create(
        model=GPT5_MODEL,
        input=[
            {
                "role": "system",
                "content": [
                    {"type": "input_text", "text": "Bạn là chuyên gia phân tích livestream bán hàng."}
                ]
            },
            {
                "role": "user",
                "content": [
                    {"type": "input_text", "text": prompt}
                ]
            }
        ],
        max_output_tokens=2048
    )

    parsed = safe_json_load(resp.output_text)

    if parsed and "video_insights" in parsed:
        return parsed

    return {
        "video_insights": [
            {
                "title": "Không thể phân tích",
                "content": "GPT không trả về đúng định dạng mong muốn."
            }
        ]
    }


def save_reports(video_name, r1, r2_raw, r2_gpt, r3_raw, r3_gpt):
    out_dir = os.path.join("report", video_name)
    os.makedirs(out_dir, exist_ok=True)

    def dump(name, obj):
        with open(os.path.join(out_dir, name), "w", encoding="utf-8") as f:
            json.dump(obj, f, ensure_ascii=False, indent=2)

    dump("report_1_timeline.json", r1)
    dump("report_2_phase_insights_raw.json", r2_raw)
    dump("report_2_phase_insights_gpt.json", r2_gpt)
    dump("report_3_video_insights_raw.json", r3_raw)
    dump("report_3_video_insights_gpt.json", r3_gpt)


PROMPT_REPORT_3_JA = """
あなたはライブコマース動画全体の構成とパフォーマンスを分析しています。

提供される情報：
- フェーズタイプごとの集計パフォーマンス
- 詳細な時系列データは含まれません

タスク：
- 動画構成の強みと弱みを分析する
- どのフェーズタイプが成果に貢献しているか、または妨げているかを説明する
- 全体構成や進行に関する改善点を提案する

ルール：
- 数値を捏造しない
- group_id を直接言及しない
- 3〜5個の簡潔なインサイトを出力する

出力形式（JSON）：
{
  "video_insights": [
    {
      "title": "短いタイトル",
      "content": "数文の説明"
    }
  ]
}

入力：
{data}
""".strip()


PROMPT_REPORT_2_JA = """
あなたはライブコマース分析の専門家です。

以下は、あるフェーズの説明と、
同タイプの過去ベストフェーズとの指標比較結果です。

ルール：
- 指標を再計算しない
- データを捏造しない
- 改善できる点のみを述べる
- 抽象的な表現を避け、具体的に書く

2〜4個の箇条書きで出力してください。

入力：
{data}
""".strip()


def build_report_1_timeline_ja(phase_units):
    out = []

    for p in phase_units:
        start = p["metric_timeseries"]["start"]
        end   = p["metric_timeseries"]["end"]

        out.append({
            "phase_index": p["phase_index"],
            "group_id": p.get("group_id"),
            "phase_description_ja": p.get("phase_description_ja"),
            "time_range": p["time_range"],
            "metrics": {
                "view_start": start.get("viewer_count"),
                "view_end": end.get("viewer_count"),
                "like_start": start.get("like_count"),
                "like_end": end.get("like_count"),
                "delta_view": (
                    end.get("viewer_count") - start.get("viewer_count")
                    if start.get("viewer_count") is not None
                       and end.get("viewer_count") is not None
                    else None
                ),
                "delta_like": (
                    end.get("like_count") - start.get("like_count")
                    if start.get("like_count") is not None
                       and end.get("like_count") is not None
                    else None
                )
            }
        })

    return out

def build_report_2_phase_insights_raw_ja(phase_units, best_data):
    out = []

    for p in phase_units:
        gid = str(p.get("group_id"))
        if not gid:
            continue

        best_group = best_data["groups"].get(gid)
        if not best_group or not best_group["phases"]:
            continue

        best = best_group["phases"][0]

        cur = extract_attention_metrics(p)
        ref = best["metrics"]

        findings = []

        if cur["view_velocity"] is not None and ref["view_velocity"] is not None:
            if cur["view_velocity"] < ref["view_velocity"]:
                findings.append("視聴者増加速度がベストフェーズより低い")

        if cur["like_per_viewer"] is not None and ref["like_per_viewer"] is not None:
            if cur["like_per_viewer"] < ref["like_per_viewer"]:
                findings.append("視聴者あたりのいいね率が低い")

        out.append({
            "phase_index": p["phase_index"],
            "group_id": gid,
            "phase_description_ja": p["phase_description_ja"],
            "current_metrics": cur,
            "benchmark_metrics": ref,
            "findings": findings
        })

    return out

def rewrite_report_2_with_gpt_ja(raw_items):
    out = []

    for item in raw_items:
        payload = json.dumps(item, ensure_ascii=False)

        insight_text = None

        for attempt in range(1, MAX_RETRY + 1):
            resp = client.responses.create(
                model=GPT5_MODEL,
                input=[
                    {
                        "role": "system",
                        "content": [
                            {"type": "input_text", "text": "あなたはライブコマース分析の専門家です。"}
                        ]
                    },
                    {
                        "role": "user",
                        "content": [
                            {"type": "input_text", "text": PROMPT_REPORT_2_JA.format(data=payload)}
                        ]
                    }
                ],
                max_output_tokens=2048
            )

            text = resp.output_text.strip() if resp.output_text else ""
            if text:
                insight_text = text
                break

            time.sleep(2 * attempt)

        if not insight_text:
            insight_text = (
                "このフェーズについては、現時点では明確な改善点を特定できません。"
            )

        out.append({
            "phase_index": item["phase_index"],
            "group_id": item["group_id"],
            "insight_ja": insight_text
        })

    return out


def build_report_3_video_insights_raw_ja(phase_units):
    groups = {}

    for p in phase_units:
        gid = str(p.get("group_id"))
        if not gid:
            continue

        m = extract_attention_metrics(p)

        g = groups.setdefault(gid, {
            "phase_count": 0,
            "total_delta_view": 0,
            "total_delta_like": 0
        })

        g["phase_count"] += 1
        if m["delta_view"] is not None:
            g["total_delta_view"] += m["delta_view"]
        if m["delta_like"] is not None:
            g["total_delta_like"] += m["delta_like"]

    return {
        "total_phases": len(phase_units),
        "group_performance": [
            {
                "group_id": gid,
                "phase_count": g["phase_count"],
                "total_delta_view": g["total_delta_view"],
                "total_delta_like": g["total_delta_like"]
            }
            for gid, g in groups.items()
        ]
    }


def rewrite_report_3_with_gpt_ja(raw_video_insight):
    payload = json.dumps(raw_video_insight, ensure_ascii=False)

    prompt = PROMPT_REPORT_3_JA.replace("{data}", payload)

    resp = client.responses.create(
        model=GPT5_MODEL,
        input=[
            {
                "role": "system",
                "content": [
                    {"type": "input_text", "text": "あなたはライブコマース分析の専門家です。"}
                ]
            },
            {
                "role": "user",
                "content": [
                    {"type": "input_text", "text": prompt}
                ]
            }
        ],
        max_output_tokens=2048
    )

    parsed = safe_json_load(resp.output_text)
    if parsed and "video_insights" in parsed:
        return parsed

    return {
        "video_insights": [
            {
                "title": "分析不可",
                "content": "モデルから有効な分析結果を取得できませんでした。"
            }
        ]
    }




# ── ZH-TW Report Prompts and Functions ──

PROMPT_REPORT_2_ZHTW = """
你是直播帶貨分析的專家。

以下是某個Phase的描述，以及與同類型歷史最佳Phase的指標比較結果。

規則：
- 不要重新計算指標
- 不要捏造數據
- 只說明可以改善的地方
- 避免抽象表達，要具體

請用2到4個要點輸出。

輸入：
{data}
""".strip()

PROMPT_REPORT_3_ZHTW = """
你正在分析一個直播帶貨影片的整體結構和表現。

提供的資訊：
- 按Phase類型匯總的表現數據
- 不包含詳細的時間序列數據

任務：
- 分析影片結構的優勢和劣勢
- 說明哪些Phase類型對成果有貢獻或造成阻礙
- 提出整體結構和進行方式的改善建議

規則：
- 不要捏造數據
- 不要直接提及group_id
- 輸出3到5個簡潔的洞察

輸出格式（JSON）：
{
  "video_insights": [
    {
      "title": "簡短標題",
      "content": "幾句話的說明"
    }
  ]
}

輸入：
{data}
""".strip()


def build_report_1_timeline_zhtw(phase_units):
    out = []

    for p in phase_units:
        start = p["metric_timeseries"]["start"]
        end   = p["metric_timeseries"]["end"]

        out.append({
            "phase_index": p["phase_index"],
            "group_id": p.get("group_id"),
            "phase_description_zhtw": p.get("phase_description_zhtw"),
            "time_range": p["time_range"],
            "metrics": {
                "view_start": start.get("viewer_count"),
                "view_end": end.get("viewer_count"),
                "like_start": start.get("like_count"),
                "like_end": end.get("like_count"),
                "delta_view": (
                    end.get("viewer_count") - start.get("viewer_count")
                    if start.get("viewer_count") is not None
                       and end.get("viewer_count") is not None
                    else None
                ),
                "delta_like": (
                    end.get("like_count") - start.get("like_count")
                    if start.get("like_count") is not None
                       and end.get("like_count") is not None
                    else None
                )
            }
        })

    return out

def build_report_2_phase_insights_raw_zhtw(phase_units, best_data):
    out = []

    for p in phase_units:
        gid = str(p.get("group_id"))
        if not gid:
            continue

        best_group = best_data["groups"].get(gid)
        if not best_group or not best_group["phases"]:
            continue

        best = best_group["phases"][0]

        cur = extract_attention_metrics(p)
        ref = best["metrics"]

        findings = []

        if cur["view_velocity"] is not None and ref["view_velocity"] is not None:
            if cur["view_velocity"] < ref["view_velocity"]:
                findings.append("觀眾增長速度低於最佳Phase")

        if cur["like_per_viewer"] is not None and ref["like_per_viewer"] is not None:
            if cur["like_per_viewer"] < ref["like_per_viewer"]:
                findings.append("每位觀眾的按讚率偏低")

        out.append({
            "phase_index": p["phase_index"],
            "group_id": gid,
            "phase_description_zhtw": p["phase_description_zhtw"],
            "current_metrics": cur,
            "benchmark_metrics": ref,
            "findings": findings
        })

    return out

def rewrite_report_2_with_gpt_zhtw(raw_items):
    out = []

    for item in raw_items:
        payload = json.dumps(item, ensure_ascii=False)

        insight_text = None

        for attempt in range(1, MAX_RETRY + 1):
            resp = client.responses.create(
                model=GPT5_MODEL,
                input=[
                    {
                        "role": "system",
                        "content": [
                            {"type": "input_text", "text": "你是直播帶貨分析的專家。"}
                        ]
                    },
                    {
                        "role": "user",
                        "content": [
                            {"type": "input_text", "text": PROMPT_REPORT_2_ZHTW.format(data=payload)}
                        ]
                    }
                ],
                max_output_tokens=2048
            )

            text = resp.output_text.strip() if resp.output_text else ""
            if text and not is_gpt_report_2_invalid(text):
                insight_text = text
                break

            time.sleep(2 * attempt)

        if not insight_text:
            insight_text = (
                "此Phase目前沒有足夠的明顯信號來提出具體建議。"
                "當有更多直播數據時，系統將提供更詳細的建議。"
            )

        out.append({
            "phase_index": item["phase_index"],
            "group_id": item["group_id"],
            "insight_zhtw": insight_text
        })

    return out


def build_report_3_video_insights_raw_zhtw(phase_units):
    groups = {}

    for p in phase_units:
        gid = str(p.get("group_id"))
        if not gid:
            continue

        m = extract_attention_metrics(p)

        g = groups.setdefault(gid, {
            "phase_count": 0,
            "total_delta_view": 0,
            "total_delta_like": 0
        })

        g["phase_count"] += 1
        if m["delta_view"] is not None:
            g["total_delta_view"] += m["delta_view"]
        if m["delta_like"] is not None:
            g["total_delta_like"] += m["delta_like"]

    return {
        "total_phases": len(phase_units),
        "group_performance": [
            {
                "group_id": gid,
                "phase_count": g["phase_count"],
                "total_delta_view": g["total_delta_view"],
                "total_delta_like": g["total_delta_like"]
            }
            for gid, g in groups.items()
        ]
    }


def rewrite_report_3_with_gpt_zhtw(raw_video_insight):
    payload = json.dumps(raw_video_insight, ensure_ascii=False)

    prompt = PROMPT_REPORT_3_ZHTW.replace("{data}", payload)

    resp = client.responses.create(
        model=GPT5_MODEL,
        input=[
            {
                "role": "system",
                "content": [
                    {"type": "input_text", "text": "你是直播帶貨分析的專家。"}
                ]
            },
            {
                "role": "user",
                "content": [
                    {"type": "input_text", "text": prompt}
                ]
            }
        ],
        max_output_tokens=2048
    )

    parsed = safe_json_load(resp.output_text)
    if parsed and "video_insights" in parsed:
        return parsed

    return {
        "video_insights": [
            {
                "title": "無法分析",
                "content": "模型未能返回有效的分析結果。"
            }
        ]
    }


def main_report_zhtw():
    """
    Entry point for generating ZH-TW (Traditional Chinese) reports.
    Called from the pipeline when user language is zh-TW.
    """
    video_name = os.path.splitext(os.path.basename(VIDEO_PATH))[0]
    phase_path = os.path.join("phase_units", f"{video_name}.json")

    if not os.path.exists(phase_path):
        print(f"[ERROR] Phase file not found: {phase_path}")
        return

    with open(phase_path, "r", encoding="utf-8") as f:
        phase_units = json.load(f)

    # STEP 5.5 – REBUILD PHASE DESCRIPTION (ZH-TW)
    print("=== STEP 5.5 – REBUILD PHASE DESCRIPTION (ZH-TW) ===")
    phase_units = rebuild_phase_descriptions_zhtw(phase_units)

    # STEP 7 – EMBEDDING + GROUPING (ZH-TW)
    print("=== STEP 7 – EMBED + GROUP (ZH-TW) ===")
    phase_units = embed_phase_descriptions_zhtw(phase_units)

    groups = load_global_groups_zhtw()
    phase_units, groups = assign_phases_to_groups(phase_units, groups, embedding_key="embedding_zhtw")
    save_global_groups_zhtw(groups)

    # STEP 8 – BEST PHASE (ZH-TW MEMORY)
    print("=== STEP 8 – GROUP BEST PHASES (ZH-TW) ===")
    best_data = load_group_best_phases_zhtw()
    best_data = update_group_best_phases(
        phase_units,
        best_data,
        video_name
    )
    save_group_best_phases_zhtw(best_data)

    # STEP 9 – BUILD REPORTS (ZH-TW)
    print("=== STEP 9 – BUILD REPORTS (ZH-TW) ===")

    r1 = build_report_1_timeline_zhtw(phase_units)

    r2_raw = build_report_2_phase_insights_raw_zhtw(
        phase_units, best_data
    )
    r2_gpt = rewrite_report_2_with_gpt_zhtw(r2_raw)

    r3_raw = build_report_3_video_insights_raw_zhtw(phase_units)
    r3_gpt = rewrite_report_3_with_gpt_zhtw(r3_raw)

    save_reports(
        video_name,
        r1,
        r2_raw,
        r2_gpt,
        r3_raw,
        r3_gpt
    )

    print(f"[DONE] ZH-TW reports generated for video: {video_name}")


# =============================
# MAIN
# =============================

def main():

    global OUT_DIR

    print("===== STEP 0 – EXTRACT FRAMES =====")
    OUT_DIR = extract_frames_step0(
        video_path=VIDEO_PATH,
        fps=1,              # giữ đúng như file old
        frames_root="frames"
    )

    # model = YOLO("yolov8x.pt")
    # model = YOLO("yolov8m.pt")
    model = YOLO("yolov8s.pt", verbose=False)

    hist_scores, absdiff_scores = compute_raw_scores()
    peaks, smooth, mix, th = detect_candidates(hist_scores, absdiff_scores)

    confirmed = confirm_boundaries(peaks, model)

    total_frames = len(os.listdir(OUT_DIR))

    merged = merge_close_boundaries(confirmed, min_gap=3)
    filtered = filter_min_phase(merged, total_frames, min_len=25)
    filtered = apply_max_phase(filtered, total_frames, max_len=150)

    keyframes = select_keyframes(filtered)

    rep_frames = pick_representative_frames(model, keyframes, total_frames)

    print("\nCandidate peaks:", peaks)
    print("YOLO confirmed boundaries:", confirmed)
    print("Merged:", merged)
    print("Filtered by phase length:", filtered)
    print("Keyframes:", keyframes)
    print("Representative frames:", rep_frames)




    phase_stats = extract_phase_stats(keyframes, total_frames)

    print("\n=== PHASE STATS (GPT VISION) ===")
    for p in phase_stats:
        print(f"\nPHASE {p['phase_index']}")
        print("start_frame:", p["phase_start_frame"], "used:", p["phase_start_used_frame"])
        print("start_stats:", p["start"])
        print("end_frame:", p["phase_end_frame"], "used:", p["phase_end_used_frame"])
        print("end_stats:", p["end"])


     # ===== STEP 3 – AUDIO → TEXT =====
    print("\n=== PHASE AUDIO TO TEXT STEP 3 ===")
    audio_dir = extract_audio_chunks()
    transcribe_audio_chunks(audio_dir)

    print("DONE AUDIO + FRAME PIPELINE")

      # ===== STEP 4 – KEY FRAME → IMAGE CAPTION =====
    print("\n=== STEP 4 – IMAGE CAPTION ===")
    keyframe_captions = caption_keyframes(rep_frames)

    # for c in keyframe_captions:
    #     print(f"\nFRAME {c['frame_index']} ({c['image']})")
    #     print("caption:", c["caption"])

    print("\n=== STEP 5 – BUILD PHASE UNITS ===")
    phase_units = build_phase_units(
        keyframes=keyframes,
        rep_frames=rep_frames,
        keyframe_captions=keyframe_captions,
        phase_stats=phase_stats,
        total_frames=total_frames
    )

    save_phase_units_to_json(phase_units)

    print("\n=== STEP 6 – BUILD PHASE DESCRIPTION ===")
    phase_units = build_phase_descriptions(phase_units)
    save_phase_units_to_json(phase_units)


    print("\n=== STEP 7 – GLOBAL PHASE GROUPING ===")
    # 7.1 embed phase descriptions
    phase_units = embed_phase_descriptions(phase_units)
    save_phase_units_to_json(phase_units)

    # 7.2 load global groups
    groups = load_global_groups()

    # 7.3 assign phases
    phase_units, groups = assign_phases_to_groups(phase_units, groups)
    save_phase_units_to_json(phase_units)

    # 7.4 save global group memory
    save_global_groups(groups)


    print("\n=== STEP 8 – GROUP BEST PHASES (ATTENTION) ===")

    video_id = os.path.splitext(os.path.basename(VIDEO_PATH))[0]

    best_data = load_group_best_phases()
    best_data = update_group_best_phases(
        phase_units,
        best_data,
        video_id
    )
    save_group_best_phases(best_data)


    print("\n=== STEP 9 – BUILD REPORTS + GPT REWRITE ===")
    best_data = load_group_best_phases()

    r1 = build_report_1_timeline(phase_units)

    r2_raw = build_report_2_phase_insights_raw(phase_units, best_data)
    r2_gpt = rewrite_report_2_with_gpt(r2_raw)

    r3_raw = build_report_3_video_insights_raw(phase_units)
    r3_gpt = rewrite_report_3_with_gpt(r3_raw)

    video_name = os.path.splitext(os.path.basename(VIDEO_PATH))[0]
    save_reports(video_name, r1, r2_raw, r2_gpt, r3_raw, r3_gpt)

def main_report_jp():
    """
    Entry-point để sinh BÁO CÁO TIẾNG NHẬT
    - Không chạy lại video / frame / audio
    - Bắt đầu từ checkpoint phase_units.json (STEP 5 output)
    """

    video_name = os.path.splitext(os.path.basename(VIDEO_PATH))[0]
    phase_path = os.path.join("phase", video_name, "phase_units.json")

    if not os.path.exists(phase_path):
        raise RuntimeError(f"Missing phase_units.json: {phase_path}")

    print(f"[LOAD] {phase_path}")
    with open(phase_path, "r", encoding="utf-8") as f:
        phase_units = json.load(f)

    # =========================
    # STEP 5.5 – REBUILD PHASE DESCRIPTION (JP)
    # =========================
    print("=== STEP 5.5 – REBUILD PHASE DESCRIPTION (JP) ===")
    phase_units = rebuild_phase_descriptions_ja(phase_units)

    # =========================
    # STEP 7 – EMBEDDING + GROUPING (JP)
    # =========================
    print("=== STEP 7 – EMBED + GROUP (JP) ===")
    phase_units = embed_phase_descriptions_ja(phase_units)

    groups = load_global_groups_ja()
    phase_units, groups = assign_phases_to_groups(phase_units, groups, embedding_key="embedding_ja")
    save_global_groups_ja(groups)

    # =========================
    # STEP 8 – BEST PHASE (JP MEMORY)
    # =========================
    print("=== STEP 8 – GROUP BEST PHASES (JP) ===")
    best_data = load_group_best_phases_ja()
    best_data = update_group_best_phases(
        phase_units,
        best_data,
        video_name
    )
    save_group_best_phases_ja(best_data)

    # =========================
    # STEP 9 – BUILD REPORTS (JP)
    # =========================
    print("=== STEP 9 – BUILD REPORTS (JP) ===")

    r1 = build_report_1_timeline_ja(phase_units)

    r2_raw = build_report_2_phase_insights_raw_ja(
        phase_units, best_data
    )
    r2_gpt = rewrite_report_2_with_gpt_ja(r2_raw)

    r3_raw = build_report_3_video_insights_raw_ja(phase_units)
    r3_gpt = rewrite_report_3_with_gpt_ja(r3_raw)

    save_reports(
        video_name,
        r1,
        r2_raw,
        r2_gpt,
        r3_raw,
        r3_gpt
    )

    print(f"[DONE] Japanese reports generated for video: {video_name}")


if __name__ == "__main__":
    # main_report_jp()

    main()
