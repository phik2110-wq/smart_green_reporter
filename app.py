import base64
from datetime import datetime, timedelta
from io import BytesIO
import json
import os
import sqlite3

import folium
from PIL import Image
import requests
import streamlit as st
from streamlit_folium import st_folium

# ============================================================
# CONFIGURATION & SECRETS
# ============================================================

st.set_page_config(
    page_title="Urban GreenEye AI",
    page_icon="🌿",
    layout="wide",
    initial_sidebar_state="expanded",
)

DB_FILE = "reports.db"
UPLOAD_DIR = "uploaded_images"
os.makedirs(UPLOAD_DIR, exist_ok=True)

CF_ACCOUNT_ID = st.secrets.get(
    "CLOUDFLARE_ACCOUNT_ID", os.getenv("CLOUDFLARE_ACCOUNT_ID", "")
)
CF_AUTH_TOKEN = st.secrets.get(
    "CLOUDFLARE_AUTH_TOKEN", os.getenv("CLOUDFLARE_AUTH_TOKEN", "")
)
CF_MODEL = "@cf/meta/llama-3.2-11b-vision-instruct"
STAFF_PIN = st.secrets.get("STAFF_PIN", os.getenv("STAFF_PIN", "1234"))

# ============================================================
# DATABASE SETUP
# ============================================================


def get_conn():
  conn = sqlite3.connect(DB_FILE, check_same_thread=False)
  conn.row_factory = sqlite3.Row
  return conn


def init_db():
  conn = get_conn()
  conn.execute("""
        CREATE TABLE IF NOT EXISTS reports (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            image_path TEXT,
            description TEXT DEFAULT '',
            location TEXT DEFAULT '',
            status TEXT DEFAULT 'Chờ AI',
            ai_result TEXT DEFAULT '',
            ai_raw_json TEXT DEFAULT '',
            ai_analyzed INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
  conn.commit()
  conn.close()


def add_column_if_missing(column_name, column_type, default_sql=None):
  conn = get_conn()
  columns = [
      r["name"] for r in conn.execute("PRAGMA table_info(reports)").fetchall()
  ]
  if column_name not in columns:
    sql = f"ALTER TABLE reports ADD COLUMN {column_name} {column_type}"
    if default_sql:
      sql += f" DEFAULT {default_sql}"
    conn.execute(sql)
    conn.commit()
  conn.close()


def migrate_db():
  init_db()
  add_column_if_missing("ai_raw_json", "TEXT", "''")
  add_column_if_missing("ai_analyzed", "INTEGER", "0")


def cleanup_old_spam():
  conn = get_conn()
  cutoff = datetime.now() - timedelta(days=7)

  rows = conn.execute(
      """
        SELECT id, image_path FROM reports
        WHERE status = 'Spam/Từ chối' AND datetime(created_at) < datetime(?)
    """,
      (cutoff.strftime("%Y-%m-%d %H:%M:%S"),),
  ).fetchall()

  for row in rows:
    path = row["image_path"]
    if path and os.path.isfile(path):
      try:
        os.remove(path)
      except OSError:
        pass

  conn.execute(
      """
        DELETE FROM reports
        WHERE status = 'Spam/Từ chối' AND datetime(created_at) < datetime(?)
    """,
      (cutoff.strftime("%Y-%m-%d %H:%M:%S"),),
  )
  conn.commit()
  conn.close()


migrate_db()
cleanup_old_spam()

# ============================================================
# CLOUDFLARE AI ENGINE
# ============================================================


def cloudflare_configured():
  return bool(CF_ACCOUNT_ID and CF_AUTH_TOKEN)


def cloudflare_url():
  return (
      f"https://api.cloudflare.com/client/v4/accounts/"
      f"{CF_ACCOUNT_ID}/ai/run/{CF_MODEL}"
  )


def agree_to_meta_license():
  if not cloudflare_configured():
    return False, "Chưa cấu hình API Keys."

  try:
    response = requests.post(
        cloudflare_url(),
        headers={
            "Authorization": f"Bearer {CF_AUTH_TOKEN}",
            "Content-Type": "application/json",
        },
        json={"prompt": "agree"},
        timeout=60,
    )
    data = response.json()
    if response.ok and data.get("success"):
      return True, "Model đã được kích hoạt thành công!"
    return False, json.dumps(data.get("errors", data), ensure_ascii=False)
  except requests.RequestException as e:
    return False, f"Lỗi kết nối: {e}"


def prepare_image(image_path, max_side=1024, quality=82):
  img = Image.open(image_path).convert("RGB")
  img.thumbnail((max_side, max_side))
  buffer = BytesIO()
  img.save(buffer, format="JPEG", quality=quality, optimize=True)
  return buffer.getvalue()


def parse_ai_text(text):
  text = (text or "").strip()
  cleaned = text.replace("```json", "").replace("```", "").strip()

  try:
    data = json.loads(cleaned)
    if isinstance(data, dict):
      return data
  except Exception:
    pass

  upper = text.upper()
  is_spam = any(
      k in upper for k in ["KHÔNG PHẢI RÁC", "SPAM", "KHÔNG CÓ RÁC", "CLEAN"]
  )
  is_waste = not is_spam

  return {
      "contains_waste": is_waste,
      "is_waste_amount_sufficient": is_waste,
      "severity": "Nặng" if "NẶNG" in upper else "Trung bình",
      "waste_type": "Rác sinh hoạt",
      "description": text,
  }


def analyze_image_with_cloudflare(image_path):
  if not cloudflare_configured():
    return {"success": False, "error": "Chưa cấu hình Cloudflare API."}

  try:
    image_bytes = prepare_image(image_path)
    image_array = list(image_bytes)

    prompt = """
Bạn là AI kiểm tra ảnh môi trường đô thị.

NHIỆM VỤ:
Nhận diện rác thải, bãi rác tự phát, bao nilon, xà bần, đồ cũ vứt bỏ.

QUY TẮC:
- Nếu thấy RÁC THẢI -> contains_waste: true, is_waste_amount_sufficient: true.
- Chỉ đặt contains_waste: false nếu ảnh hoàn toàn sạch vẽ, ảnh người, cảnh vật không có rác.

Trả về duy nhất JSON hợp lệ:
{
  "contains_waste": true,
  "is_waste_amount_sufficient": true,
  "severity": "Nhẹ" hoặc "Trung bình" hoặc "Nặng",
  "waste_type": "Rác sinh hoạt",
  "description": "Mô tả điểm rác"
}
"""

    payload = {
        "prompt": prompt,
        "image": image_array,
        "max_tokens": 300,
        "temperature": 0.1,
    }

    response = requests.post(
        cloudflare_url(),
        headers={
            "Authorization": f"Bearer {CF_AUTH_TOKEN}",
            "Content-Type": "application/json",
        },
        json=payload,
        timeout=120,
    )

    data = response.json()
    if not response.ok or not data.get("success", False):
      return {
          "success": False,
          "error": json.dumps(data.get("errors", data), ensure_ascii=False),
      }

    result = data.get("result", {})
    text = result.get("response", "") or result.get("text", "")
    parsed = parse_ai_text(text)

    return {"success": True, "parsed": parsed, "raw": data, "text": text}

  except Exception as e:
    return {"success": False, "error": f"Lỗi AI: {e}"}


def save_ai_result(report_id, analysis):
  """Hàm lưu kết quả và điều phối trạng thái dựa chính xác vào dữ liệu AI trả về."""
  conn = get_conn()

  if not analysis["success"]:
    conn.execute(
        "UPDATE reports SET ai_analyzed = 0, ai_result = ? WHERE id = ?",
        (analysis["error"], report_id),
    )
    conn.commit()
    conn.close()
    return

  parsed = analysis["parsed"]

  # ĐIỀU PHỐI TRẠNG THÁI: Kiểm tra tất cả kiểu dữ liệu boolean/string từ AI
  c_waste = str(parsed.get("contains_waste", "")).lower()
  c_enough = str(parsed.get("is_waste_amount_sufficient", "")).lower()

  is_valid_report = (
      parsed.get("contains_waste") is True
      or parsed.get("is_waste_amount_sufficient") is True
      or c_waste == "true"
      or c_enough == "true"
  )

  # Nếu AI xác nhận có rác -> Tự động ĐÃ DUYỆT
  status = "Đã duyệt" if is_valid_report else "Spam/Từ chối"

  # Tạo văn bản tổng hợp kết quả
  if isinstance(parsed, dict) and "description" in parsed:
    display_text = (
        f"📝 Mô tả: {parsed.get('description', '')}\n"
        f"🏷️ Loại rác: {parsed.get('waste_type', 'Rác hỗn hợp')} | "
        f"⚠️ Mức độ: {parsed.get('severity', 'Trung bình')}"
    )
  else:
    display_text = analysis["text"]

  conn.execute(
      """
        UPDATE reports
        SET status = ?,
            ai_result = ?,
            ai_raw_json = ?,
            ai_analyzed = 1
        WHERE id = ?
    """,
      (
          status,
          display_text,
          json.dumps(analysis["raw"], ensure_ascii=False),
          report_id,
      ),
  )

  conn.commit()
  conn.close()


# ============================================================
# NAVIGATION & UI
# ============================================================

st.sidebar.title("🌿 Urban GreenEye")
menu = st.sidebar.radio(
    "Chức năng",
    ["📷 Gửi báo cáo", "📋 Quản lý & AI", "🗑️ Quản lý Spam", "⚙️ Cài đặt AI"],
)

st.title("🌿 Urban GreenEye AI")

# --- 1. GỬI BÁO CÁO (CHỌN BẢN ĐỒ) ---
if menu == "📷 Gửi báo cáo":
  st.header("📷 Gửi báo cáo điểm xả rác")

  uploaded_file = st.file_uploader(
      "Ảnh hiện trạng", type=["jpg", "jpeg", "png", "webp"]
  )

  st.write("📍 **Chọn vị trí trên bản đồ:**")
  m = folium.Map(location=[10.762622, 106.660172], zoom_start=12)
  m.add_child(folium.LatLngPopup())
  map_data = st_folium(m, height=280, use_container_width=True)

  selected_location = ""
  if map_data and map_data.get("last_clicked"):
    lat, lng = map_data["last_clicked"]["lat"], map_data["last_clicked"]["lng"]
    selected_location = f"Tọa độ: {lat:.6f}, {lng:.6f}"
    st.success(f"📌 Đã chọn: {selected_location}")

  manual_location = st.text_input(
      "Hoặc nhập địa chỉ:", placeholder="Ví dụ: Số 10 đường ABC..."
  )
  description = st.text_area("Mô tả chi tiết")
  final_location = manual_location.strip() or selected_location

  if st.button("🚀 Gửi báo cáo ngay", type="primary", use_container_width=True):
    if not uploaded_file or not final_location:
      st.warning("⚠️ Vui lòng chọn ảnh và chọn vị trí trên bản đồ!")
      st.stop()

    image = Image.open(uploaded_file).convert("RGB")
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    image_path = os.path.join(UPLOAD_DIR, f"{timestamp}.jpg")
    image.save(image_path, format="JPEG", quality=90)

    conn = get_conn()
    cursor = conn.execute(
        """
            INSERT INTO reports (image_path, description, location, status, ai_result)
            VALUES (?, ?, ?, 'Đang phân tích', 'AI đang phân tích...')
        """,
        (image_path, description.strip(), final_location),
    )
    report_id = cursor.lastrowid
    conn.commit()
    conn.close()

    if cloudflare_configured():
      with st.spinner("🤖 AI đang phân tích và tự động điều phối..."):
        analysis = analyze_image_with_cloudflare(image_path)
        save_ai_result(report_id, analysis)

    st.success(f"🎉 Báo cáo #{report_id} đã gửi và xử lý thành công!")
    st.rerun()

# --- 2. QUẢN LÝ BÁO CÁO ---
elif menu == "📋 Quản lý & AI":
  st.header("📋 Quản lý báo cáo")
  if st.text_input("🔐 Mã PIN", type="password", key="m1") != STAFF_PIN:
    st.warning("Vui lòng nhập mã PIN quản trị.")
    st.stop()

  conn = get_conn()
  rows = conn.execute("SELECT * FROM reports ORDER BY id DESC").fetchall()
  conn.close()

  for r in rows:
    icon = (
        "✅"
        if r["status"] == "Đã duyệt"
        else ("🚫" if r["status"] == "Spam/Từ chối" else "⏳")
    )
    with st.expander(f"{icon} Báo cáo #{r['id']} — {r['location']}"):
      c1, c2 = st.columns([1, 1])
      with c1:
        if r["image_path"] and os.path.isfile(r["image_path"]):
          st.image(r["image_path"], use_container_width=True)
      with c2:
        st.write(f"**Trạng thái:** `{r['status']}`")
        st.write(f"**Mô tả người dùng:** {r['description']}")
        st.info(f"**Kết quả phân tích AI:**\n{r['ai_result']}")

# --- 3. QUẢN LÝ SPAM ---
elif menu == "🗑️ Quản lý Spam":
  st.header("🗑️ Danh sách Spam")
  if st.text_input("🔐 Mã PIN", type="password", key="m2") != STAFF_PIN:
    st.warning("Vui lòng nhập mã PIN quản trị.")
    st.stop()

  conn = get_conn()
  rows = conn.execute(
      "SELECT * FROM reports WHERE status = 'Spam/Từ chối' ORDER BY id DESC"
  ).fetchall()
  conn.close()

  for r in rows:
    with st.expander(f"🚫 Spam #{r['id']} — {r['location']}"):
      st.write(f"**Lý do:** {r['ai_result']}")

# --- 4. CÀI ĐẶT AI ---
elif menu == "⚙️ Cài đặt AI":
  st.header("⚙️ Cài đặt AI")
  if st.text_input("🔐 Mã PIN Cài đặt", type="password", key="m3") != STAFF_PIN:
    st.warning("Vui lòng nhập mã PIN để cấu hình AI.")
    st.stop()

  st.success("🔓 Đã mở khóa Cài đặt AI")
  if st.button("☁️ Kích hoạt Vision AI License", type="primary"):
    ok, msg = agree_to_meta_license()
    st.success(msg) if ok else st.error(msg)
