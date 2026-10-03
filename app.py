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
# CẤU HÌNH HỆ THỐNG & BIẾN MÔI TRƯỜNG
# ============================================================

st.set_page_config(
    page_title="Urban GreenEye AI - Quản lý Ô nhiễm Đô thị",
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
# CSS TÙY CHỈNH GIẢI PHÁP
# ============================================================

st.markdown(
    """
<style>
.main-title {
    font-size: 36px;
    font-weight: 800;
    color: #1e293b;
    margin-bottom: 0px;
}
.subtitle {
    color: #64748b;
    font-size: 16px;
    margin-bottom: 25px;
}
.badge-ok {
    background-color: #dcfce7;
    color: #15803d;
    padding: 4px 12px;
    border-radius: 20px;
    font-weight: bold;
    font-size: 14px;
}
.badge-spam {
    background-color: #fee2e2;
    color: #b91c1c;
    padding: 4px 12px;
    border-radius: 20px;
    font-weight: bold;
    font-size: 14px;
}
.badge-wait {
    background-color: #fef3c7;
    color: #b45309;
    padding: 4px 12px;
    border-radius: 20px;
    font-weight: bold;
    font-size: 14px;
}
</style>
""",
    unsafe_allow_html=True,
)


# ============================================================
# XỬ LÝ CƠ SỞ DỮ LIỆU SQLITE3
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
            status TEXT DEFAULT 'Đã nhận',
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
  """Xóa các báo cáo rác/spam đã cũ hơn 7 ngày."""
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
# ENGINE CLOUDFLARE AI & THUẬT TOÁN ĐIỀU PHỐI
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
    return False, "Chưa cấu hình API Keys trên Secrets."

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
      return True, "Kích hoạt Model Vision AI thành công!"
    return False, json.dumps(data.get("errors", data), ensure_ascii=False)
  except requests.RequestException as e:
    return False, f"Lỗi kết nối API: {e}"


def prepare_image(image_path, max_side=1024, quality=82):
  img = Image.open(image_path).convert("RGB")
  img.thumbnail((max_side, max_side))
  buffer = BytesIO()
  img.save(buffer, format="JPEG", quality=quality, optimize=True)
  return buffer.getvalue()


def parse_ai_text(text):
  """Giải mã kết quả từ AI, loại bỏ đoạn hội thoại thừa."""
  text = (text or "").strip()
  cleaned = text.replace("```json", "").replace("```", "").strip()

  # Tìm vị trí JSON trong chuỗi nếu AI trả về kèm văn bản giải thích
  start_idx = cleaned.find("{")
  end_idx = cleaned.rfind("}")

  if start_idx != -1 and end_idx != -1 and end_idx > start_idx:
    json_str = cleaned[start_idx : end_idx + 1]
    try:
      data = json.loads(json_str)
      if isinstance(data, dict):
        return data
    except Exception:
      pass

  # Mặc định phân tích theo từ khóa dự phòng
  upper = text.upper()
  has_no_waste = any(
      k in upper
      for k in [
          "KHÔNG PHẢI RÁC",
          "KHÔNG CÓ RÁC",
          "KHONG PHAI RAC",
          "NO WASTE",
          "CLEAN",
      ]
  )
  has_waste = not has_no_waste

  return {
      "contains_waste": has_waste,
      "severity": "Nặng" if "NẶNG" in upper else "Trung bình",
      "waste_type": "Rác sinh hoạt hỗn hợp",
      "action_plan": (
          "Phân công công nhân vệ sinh dọn dẹp và gom rác trong ngày."
      ),
  }


def analyze_image_with_cloudflare(image_path):
  if not cloudflare_configured():
    return {"success": False, "error": "Chưa cấu hình API Cloudflare."}

  try:
    image_bytes = prepare_image(image_path)
    image_array = list(image_bytes)

    # PROMPT ÉP AI BẮT BUỘC TRẢ VỀ ĐÚNG ĐỊNH DẠNG TÓM TẮT
    prompt = """
Bạn là hệ thống AI giám sát môi trường đô thị.

NHIỆM VỤ:
Phân tích ảnh và xác định xem có RÁC THẢI hay không. KHÔNG chào hỏi, KHÔNG giải thích dài dòng.

TRẢ VỀ DUY NHẤT 1 CHUỖI JSON THEO ĐÚNG CẤU TRÚC SAU:
{
  "contains_waste": true,
  "severity": "Nhẹ" hoặc "Trung bình" hoặc "Nặng",
  "waste_type": "Rác sinh hoạt" hoặc "Xà bần" hoặc "Rác cồng kềnh" hoặc "Rác nhựa",
  "action_plan": "Đề xuất phương án xử lý ngắn gọn (Ví dụ: Bố trí 2 công nhân thu gom / Cần xe cơ giới xúc xà bần / Lắp biển cấm đổ rác)"
}
"""

    payload = {
        "prompt": prompt,
        "image": image_array,
        "max_tokens": 250,
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
    return {"success": False, "error": f"Lỗi kết nối AI: {e}"}


def save_ai_result(report_id, analysis):
  """Hàm lưu kết quả tóm tắt và tự động điều phối báo cáo."""
  conn = get_conn()

  if not analysis["success"]:
    conn.execute(
        "UPDATE reports SET ai_analyzed = 0, ai_result = ? WHERE id = ?",
        (f"❌ Lỗi AI: {analysis['error']}", report_id),
    )
    conn.commit()
    conn.close()
    return

  parsed = analysis["parsed"]

  # ĐÁNH GIÁ CÓ RÁC HAY KHÔNG
  raw_waste = str(parsed.get("contains_waste", "")).lower()
  is_waste = parsed.get("contains_waste") is True or raw_waste == "true"

  # Điều phối trạng thái tự động
  if is_waste:
    status = "Đã duyệt"
  else:
    status = "Spam/Từ chối"

  # TẠO HIỂN THỊ TÓM TẮT NGẮN GỌN (ĐÚNG YÊU CẦU)
  if is_waste:
    display_text = (
        f"🔴 **Phát hiện rác:** CÓ RÁC\n"
        f"🏷️ **Loại rác:** {parsed.get('waste_type', 'Rác hỗn hợp')}\n"
        f"⚠️ **Mức độ:** {parsed.get('severity', 'Trung bình')}\n"
        f"🛠️ **Phương án xử lý:** {parsed.get('action_plan', 'Điều động công nhân dọn dẹp điểm rác.')}"
    )
  else:
    display_text = (
        "🟢 **Phát hiện rác:** KHÔNG CÓ RÁC\n"
        "❌ **Kết luận:** Ảnh không có vi phạm ô nhiễm hoặc không hợp lệ."
    )

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
# GIAO DIỆN CHÍNH (STREAMLIT UI)
# ============================================================

st.sidebar.title("🌿 Urban GreenEye")
st.sidebar.caption("Hệ thống AI Giám sát Đô thị")

menu = st.sidebar.radio(
    "Điều hướng hệ thống",
    [
        "📷 Gửi báo cáo mới",
        "📋 Bảng quản lý & Xử lý",
        "🗑️ Danh sách Spam",
        "⚙️ Cấu hình AI",
    ],
)

st.markdown(
    '<div class="main-title">🌿 Urban GreenEye AI</div>', unsafe_allow_html=True
)
st.markdown(
    '<div class="subtitle">Tiếp nhận báo cáo ô nhiễm, tự động chọn vị trí và'
    " điều phối bằng AI</div>",
    unsafe_allow_html=True,
)

# ------------------------------------------------------------
# 1. GỬI BÁO CÁO MỚI (CÓ BẢN ĐỒ + QUY TRÌNH 2 BƯỚC)
# ------------------------------------------------------------
if menu == "📷 Gửi báo cáo mới":
  st.header("📷 Gửi báo cáo hiện trạng rác thải")

  uploaded_file = st.file_uploader(
      " Tải ảnh điểm xả rác hiện trường:",
      type=["jpg", "jpeg", "png", "webp"],
  )

  st.write("📍 **Nhấp chuột trên bản đồ để chọn vị trí chính xác:**")

  # Tọa độ mặc định
  m = folium.Map(location=[10.762622, 106.660172], zoom_start=12)
  m.add_child(folium.LatLngPopup())
  map_data = st_folium(m, height=300, use_container_width=True)

  selected_location = ""
  if map_data and map_data.get("last_clicked"):
    lat = map_data["last_clicked"]["lat"]
    lng = map_data["last_clicked"]["lng"]
    selected_location = f"Tọa độ: {lat:.6f}, {lng:.6f}"
    st.success(f"📌 Vị trí đã chọn: {selected_location}")
  else:
    st.info("👉 Nhấp vào bất kỳ điểm nào trên bản đồ để lấy tọa độ.")

  manual_location = st.text_input(
      "Địa chỉ bổ sung (tùy chọn):",
      placeholder="Ví dụ: Trước số nhà 123 đường Nguyễn Văn A...",
  )
  description = st.text_area(
      "Mô tả thêm:", placeholder="Ví dụ: Rác sinh hoạt chất đống bốc mùi..."
  )

  final_location = manual_location.strip() or selected_location

  if st.button("🚀 Gửi báo cáo ngay", type="primary", use_container_width=True):
    if not uploaded_file:
      st.warning("⚠️ Vui lòng tải ảnh lên!")
      st.stop()

    if not final_location:
      st.warning("⚠️ Vui lòng chọn vị trí trên bản đồ hoặc nhập địa chỉ!")
      st.stop()

    # BƯỚC 1: XÁC NHẬN ĐÃ NHẬN BÁO CÁO
    image = Image.open(uploaded_file).convert("RGB")
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    image_path = os.path.join(UPLOAD_DIR, f"{timestamp}.jpg")
    image.save(image_path, format="JPEG", quality=90)

    conn = get_conn()
    cursor = conn.execute(
        """
            INSERT INTO reports (image_path, description, location, status, ai_result, ai_analyzed)
            VALUES (?, ?, ?, 'Đã nhận', 'Đã tiếp nhận báo cáo. Đang chờ AI phân tích...', 0)
        """,
        (image_path, description.strip(), final_location),
    )
    report_id = cursor.lastrowid
    conn.commit()
    conn.close()

    st.success(
        f"✅ **Đã tiếp nhận báo cáo #{report_id}!** Ảnh và vị trí đã được lưu"
        " thành công."
    )

    # BƯỚC 2: PHÂN TÍCH VÀ ĐIỀU PHỐI TỰ ĐỘNG BẰNG AI
    if cloudflare_configured():
      with st.spinner(
          "🤖 **Hệ thống AI đang phân tích ảnh và lập phương án xử"
          " lý...**"
      ):
        analysis = analyze_image_with_cloudflare(image_path)
        save_ai_result(report_id, analysis)

      st.success("🎉 **AI đã hoàn tất phân tích và điều phối báo cáo!**")
    else:
      st.warning(
          "⚠️ Chưa cấu hình Cloudflare AI. Báo cáo lưu ở trạng thái chờ."
      )

    st.rerun()

# ------------------------------------------------------------
# 2. BẢNG QUẢN LÝ BÁO CÁO (YÊU CẦU MÃ PIN)
# ------------------------------------------------------------
elif menu == "📋 Bảng quản lý & Xử lý":
  st.header("📋 Bảng quản lý báo cáo đô thị")

  pin = st.text_input("🔐 Mã PIN Quản trị", type="password", key="pin_mgr")
  if pin != STAFF_PIN:
    st.warning("Vui lòng nhập mã PIN quản trị để truy cập dữ liệu.")
    st.stop()

  conn = get_conn()
  rows = conn.execute("SELECT * FROM reports ORDER BY id DESC").fetchall()
  conn.close()

  if not rows:
    st.info("Chưa có báo cáo nào trong hệ thống.")
  else:
    for r in rows:
      status = r["status"]
      if status == "Đã duyệt":
        badge = "🟢 ĐÃ DUYỆT (CÓ RÁC)"
      elif status == "Spam/Từ chối":
        badge = "🔴 SPAM / TỪ CHỐI"
      else:
        badge = "🟡 ĐÃ NHẬN / CHỜ AI"

      with st.expander(f"Báo cáo #{r['id']} — {r['location']} | {badge}"):
        c1, c2 = st.columns([1, 1.2])
        with c1:
          if r["image_path"] and os.path.isfile(r["image_path"]):
            st.image(
                r["image_path"],
                caption=f"Ảnh báo cáo #{r['id']}",
                use_container_width=True,
            )
        with c2:
          st.write(f"📍 **Vị trí:** {r['location']}")
          st.write(f"📝 **Mô tả người dùng:** {r['description'] or 'Không có'}")
          st.write(f"🕒 **Thời gian gửi:** {r['created_at']}")
          st.markdown("---")
          st.subheader("📊 Kết quả phân tích & Điều phối AI")
          if r["ai_result"]:
            st.info(r["ai_result"])
          else:
            st.caption("Chưa có kết quả phân tích.")

# ------------------------------------------------------------
# 3. QUẢN LÝ SPAM / TỪ CHỐI (YÊU CẦU MÃ PIN)
# ------------------------------------------------------------
elif menu == "🗑️ Danh sách Spam":
  st.header("🗑️️ Báo cáo bị từ chối / Spam")

  pin = st.text_input("🔐 Mã PIN Quản trị", type="password", key="pin_spam")
  if pin != STAFF_PIN:
    st.warning("Vui lòng nhập mã PIN quản trị.")
    st.stop()

  conn = get_conn()
  rows = conn.execute(
      "SELECT * FROM reports WHERE status = 'Spam/Từ chối' ORDER BY id DESC"
  ).fetchall()
  conn.close()

  if not rows:
    st.success("Không có báo cáo spam nào.")
  else:
    for r in rows:
      with st.expander(f"🚫 Báo cáo Spam #{r['id']} — {r['location']}"):
        c1, c2 = st.columns([1, 2])
        with c1:
          if r["image_path"] and os.path.isfile(r["image_path"]):
            st.image(r["image_path"], use_container_width=True)
        with c2:
          st.write(f"📍 **Vị trí:** {r['location']}")
          st.write(f"📝 **Mô tả gửi:** {r['description']}")
          st.error(r["ai_result"])

# ------------------------------------------------------------
# 4. CẤU HÌNH AI (YÊU CẦU MÃ PIN)
# ------------------------------------------------------------
elif menu == "⚙️ Cấu hình AI":
  st.header("⚙️ Cấu hình Cloudflare AI Engine")

  pin = st.text_input("🔐 Mã PIN Cấu hình", type="password", key="pin_cfg")
  if pin != STAFF_PIN:
    st.warning("Vui lòng nhập mã PIN quản trị để cấu hình AI.")
    st.stop()

  st.success("🔓 Đã truy cập khu vực cấu hình hệ thống!")
  st.write(f"**Model đang kết nối:** `{CF_MODEL}`")

  if cloudflare_configured():
    st.success("✅ Đã kết nối thành công Cloudflare API Tokens.")
  else:
    st.error("❌ Chưa cấu hình Token trong Streamlit Secrets.")

  if st.button("☁️ Kích hoạt Meta License AI", type="primary"):
    with st.spinner("Đang gửi xác nhận đến Cloudflare..."):
      ok, msg = agree_to_meta_license()
      if ok:
        st.success(msg)
      else:
        st.error(msg)
