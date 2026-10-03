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
# CẤU HÌNH HỆ THỐNG
# ============================================================

st.set_page_config(
    page_title="Urban GreenEye AI - Quản lý Ô nhiễm Đô thị",
    page_icon="🌿",
    layout="wide",
    initial_sidebar_state="expanded",
)

DB_FILE = "reports.db"
UPLOAD_DIR = "uploaded_images"
CLEANUP_DIR = "cleanup_images"
os.makedirs(UPLOAD_DIR, exist_ok=True)
os.makedirs(CLEANUP_DIR, exist_ok=True)

CF_ACCOUNT_ID = st.secrets.get(
    "CLOUDFLARE_ACCOUNT_ID", os.getenv("CLOUDFLARE_ACCOUNT_ID", "")
)
CF_AUTH_TOKEN = st.secrets.get(
    "CLOUDFLARE_AUTH_TOKEN", os.getenv("CLOUDFLARE_AUTH_TOKEN", "")
)
CF_MODEL = "@cf/meta/llama-3.2-11b-vision-instruct"
STAFF_PIN = st.secrets.get("STAFF_PIN", os.getenv("STAFF_PIN", "1234"))

# ============================================================
# CƠ SỞ DỮ LIỆU SQLITE3
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
            assigned_to TEXT DEFAULT '',
            cleanup_image_path TEXT DEFAULT '',
            cleanup_note TEXT DEFAULT '',
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
  add_column_if_missing("assigned_to", "TEXT", "''")
  add_column_if_missing("cleanup_image_path", "TEXT", "''")
  add_column_if_missing("cleanup_note", "TEXT", "''")


migrate_db()

# ============================================================
# ENGINE CLOUDFLARE AI
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
      return True, "Kích hoạt Model thành công!"
    return False, json.dumps(data.get("errors", data), ensure_ascii=False)
  except Exception as e:
    return False, f"Lỗi kết nối: {e}"


def prepare_image(image_path, max_side=1024, quality=82):
  img = Image.open(image_path).convert("RGB")
  img.thumbnail((max_side, max_side))
  buffer = BytesIO()
  img.save(buffer, format="JPEG", quality=quality, optimize=True)
  return buffer.getvalue()


def parse_ai_text(raw_input):
  """Sửa triệt để lỗi 'dict' object has no attribute 'strip'"""
  if isinstance(raw_input, dict):
    # Nếu AI trả về sẵn một dict
    return {
        "contains_waste": raw_input.get("contains_waste", True),
        "severity": raw_input.get("severity", "Trung bình"),
        "waste_type": raw_input.get("waste_type", "Rác sinh hoạt"),
    }

  text = str(raw_input or "").strip()
  cleaned = text.replace("```json", "").replace("```", "").strip()

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

  return {
      "contains_waste": not has_no_waste,
      "severity": "Nặng" if "NẶNG" in upper else "Trung bình",
      "waste_type": "Rác sinh hoạt",
  }


def analyze_image_with_cloudflare(image_path):
  if not cloudflare_configured():
    return {"success": False, "error": "Chưa cấu hình Cloudflare API."}

  try:
    image_bytes = prepare_image(image_path)
    image_array = list(image_bytes)

    prompt = """
TRẢ VỀ DUY NHẤT JSON (KHÔNG VIẾT LỜI DẪN, KHÔNG CHÀO HỎI):
{
  "contains_waste": true,
  "severity": "Nhẹ" hoặc "Trung bình" hoặc "Nặng",
  "waste_type": "Rác sinh hoạt" hoặc "Xà bần" hoặc "Rác cồng kềnh"
}
"""

    payload = {
        "prompt": prompt,
        "image": image_array,
        "max_tokens": 150,
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
    raw_response = result.get("response", "") or result.get("text", "")

    parsed = parse_ai_text(raw_response)

    return {"success": True, "parsed": parsed, "raw": data}

  except Exception as e:
    return {"success": False, "error": f"Lỗi xử lý AI: {str(e)}"}


def save_ai_result(report_id, analysis):
  conn = get_conn()

  if not analysis["success"]:
    conn.execute(
        "UPDATE reports SET ai_analyzed = 0, ai_result = ? WHERE id = ?",
        (f"❌ Lỗi: {analysis['error']}", report_id),
    )
    conn.commit()
    conn.close()
    return

  parsed = analysis["parsed"]
  raw_waste = str(parsed.get("contains_waste", "")).lower()
  is_waste = parsed.get("contains_waste") is True or raw_waste == "true"

  status = "Đã duyệt" if is_waste else "Spam/Từ chối"

  # NỔI BẬT: CHỈ HIỂN THỊ SIÊU NGẮN GỌN (SẠCH SẼ)
  if is_waste:
    display_text = (
        f"🔴 **Phát hiện rác:** CÓ RÁC\n"
        f"🏷️ **Loại rác:** {parsed.get('waste_type', 'Rác hỗn hợp')}\n"
        f"⚠️ **Mức độ:** {parsed.get('severity', 'Trung bình')}"
    )
  else:
    display_text = (
        "🟢 **Phát hiện rác:** KHÔNG CÓ RÁC\n"
        "❌ **Kết luận:** Ảnh không có vi phạm ô nhiễm."
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
# GIAO DIỆN CHÍNH
# ============================================================

st.sidebar.title("🌿 Urban GreenEye")
menu = st.sidebar.radio(
    "Điều hướng",
    [
        "📷 Gửi báo cáo",
        "📋 Điều phối & Quản lý",
        "🧹 Đội dọn dẹp báo cáo",
        "🗑️ Báo cáo Spam & Xóa",
        "⚙️️ Cài đặt AI",
    ],
)

st.title("🌿 Urban GreenEye AI")

# ------------------------------------------------------------
# 1. GỬI BÁO CÁO (CÓ BẢN ĐỒ)
# ------------------------------------------------------------
if menu == "📷 Gửi báo cáo":
  st.header("📷 Gửi báo cáo điểm rác")

  uploaded_file = st.file_uploader(
      " Tải ảnh hiện trường:", type=["jpg", "jpeg", "png", "webp"]
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
      "Nhập địa chỉ cụ thể:", placeholder="Số nhà, tên đường..."
  )
  description = st.text_area("Mô tả thêm:")
  final_location = manual_location.strip() or selected_location

  if st.button("🚀 Gửi báo cáo", type="primary", use_container_width=True):
    if not uploaded_file or not final_location:
      st.warning("⚠️ Vui lòng tải ảnh và chọn vị trí!")
      st.stop()

    image = Image.open(uploaded_file).convert("RGB")
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    image_path = os.path.join(UPLOAD_DIR, f"{timestamp}.jpg")
    image.save(image_path, format="JPEG", quality=90)

    conn = get_conn()
    cursor = conn.execute(
        """
            INSERT INTO reports (image_path, description, location, status, ai_result)
            VALUES (?, ?, ?, 'Đã nhận', 'Đã tiếp nhận báo cáo. Đang chờ AI phân tích...')
        """,
        (image_path, description.strip(), final_location),
    )
    report_id = cursor.lastrowid
    conn.commit()
    conn.close()

    st.success(f"✅ **Đã tiếp nhận báo cáo #{report_id}!**")

    if cloudflare_configured():
      with st.spinner("🤖 **AI đang phân tích ảnh...**"):
        analysis = analyze_image_with_cloudflare(image_path)
        save_ai_result(report_id, analysis)

    st.rerun()

# ------------------------------------------------------------
# 2. ĐIỀU PHỐI & QUẢN LÝ (PHÂN CÔNG & XÓA BÁO CÁO)
# ------------------------------------------------------------
elif menu == "📋 Điều phối & Quản lý":
  st.header("📋 Quản lý & Điều phối xử lý")

  if st.text_input("🔐 Mã PIN Quản trị", type="password") != STAFF_PIN:
    st.warning("Vui lòng nhập mã PIN quản trị.")
    st.stop()

  conn = get_conn()
  rows = conn.execute(
      "SELECT * FROM reports WHERE status != 'Spam/Từ chối' ORDER BY id DESC"
  ).fetchall()
  conn.close()

  if not rows:
    st.info("Không có báo cáo hợp lệ nào.")
  else:
    st.subheader("🗑️ Xóa báo cáo hàng loạt")
    selected_to_delete = []

    for r in rows:
      col_chk, col_info = st.columns([0.1, 0.9])
      with col_chk:
        if st.checkbox("", key=f"del_{r['id']}"):
          selected_to_delete.append(r["id"])

      with col_info:
        badge = (
            "🟢 ĐÃ DUYỆT"
            if r["status"] == "Đã duyệt"
            else ("🧹 ĐANG DỌN" if r["status"] == "Đang dọn" else "✅ HOÀN THÀNH")
        )
        with st.expander(
            f"Báo cáo #{r['id']} — {r['location']} | {badge} | Đội:"
            f" {r['assigned_to'] or 'Chưa giao'}"
        ):
          c1, c2 = st.columns([1, 1])
          with c1:
            if r["image_path"] and os.path.isfile(r["image_path"]):
              st.image(r["image_path"], use_container_width=True)
          with c2:
            st.write(f"📍 **Vị trí:** {r['location']}")
            st.write(f"📝 **Mô tả:** {r['description']}")
            st.info(f"**Kết quả AI:**\n{r['ai_result']}")

            # MỤC ĐIỀU PHỐI VÀ PHÂN CÔNG DỌN DẸP
            st.markdown("---")
            st.write("👷 **Phân công & Điều phối:**")
            assigned_team = st.text_input(
                "Tên Đội/Công nhân phụ trách:",
                value=r["assigned_to"] or "",
                key=f"team_{r['id']}",
            )
            new_status = st.selectbox(
                "Cập nhật trạng thái:",
                ["Đã duyệt", "Đang dọn", "Hoàn thành"],
                index=["Đã duyệt", "Đang dọn", "Hoàn thành"].index(
                    r["status"] if r["status"] in ["Đang dọn", "Hoàn thành"] else "Đã duyệt"
                ),
                key=f"st_{r['id']}",
            )

            if st.button("💾 Lưu điều phối", key=f"save_{r['id']}"):
              conn = get_conn()
              conn.execute(
                  """
                                UPDATE reports SET assigned_to = ?, status = ? WHERE id = ?
                            """,
                  (assigned_team, new_status, r["id"]),
              )
              conn.commit()
              conn.close()
              st.success("Đã cập nhật phân công!")
              st.rerun()

    if selected_to_delete and st.button(
        f"❌ Xóa {len(selected_to_delete)} báo cáo đã chọn", type="primary"
    ):
      conn = get_conn()
      for did in selected_to_delete:
        row = conn.execute(
            "SELECT image_path FROM reports WHERE id = ?", (did,)
        ).fetchone()
        if row and row["image_path"] and os.path.isfile(row["image_path"]):
          os.remove(row["image_path"])
        conn.execute("DELETE FROM reports WHERE id = ?", (did,))
      conn.commit()
      conn.close()
      st.success("Đã xóa các báo cáo được chọn!")
      st.rerun()

# ------------------------------------------------------------
# 3. ĐỘI DỌN DẸP BÁO CÁO HOÀN THÀNH
# ------------------------------------------------------------
elif menu == "🧹 Đội dọn dẹp báo cáo":
  st.header("🧹 Đội dọn dẹp báo cáo kết quả hiện trường")

  conn = get_conn()
  active_tasks = conn.execute(
      "SELECT * FROM reports WHERE status IN ('Đã duyệt', 'Đang dọn') ORDER BY"
      " id DESC"
  ).fetchall()
  conn.close()

  if not active_tasks:
    st.success("Hiện tại không có điểm rác nào cần dọn dẹp!")
  else:
    task_options = [
        f"#{t['id']} - {t['location']} ({t['assigned_to'] or 'Chưa phân công'})"
        for t in active_tasks
    ]
    selected_task_str = st.selectbox("Chọn điểm rác vừa dọn xong:", task_options)
    task_id = int(selected_task_str.split(" - ")[0].replace("#", ""))

    cleaned_file = st.file_uploader(
        " Tải ảnh bằng chứng SẠCH RÁC sau khi dọn:",
        type=["jpg", "jpeg", "png"],
    )
    cleanup_note = st.text_area("Ghi chú thu gom (Xe gom, số bao rác...):")

    if st.button("✅ Báo cáo đã dọn xong", type="primary"):
      if not cleaned_file:
        st.warning("Vui lòng tải ảnh bằng chứng đã dọn sạch!")
        st.stop()

      img = Image.open(cleaned_file).convert("RGB")
      timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
      c_path = os.path.join(CLEANUP_DIR, f"clean_{task_id}_{timestamp}.jpg")
      img.save(c_path, format="JPEG", quality=90)

      conn = get_conn()
      conn.execute(
          """
                UPDATE reports 
                SET status = 'Hoàn thành', cleanup_image_path = ?, cleanup_note = ?
                WHERE id = ?
            """,
          (c_path, cleanup_note, task_id),
      )
      conn.commit()
      conn.close()

      st.success(f"🎉 Đã hoàn tất báo cáo dọn dẹp cho điểm #{task_id}!")
      st.rerun()

# ------------------------------------------------------------
# 4. QUẢN LÝ SPAM & XÓA
# ------------------------------------------------------------
elif menu == "🗑️ Báo cáo Spam & Xóa":
  st.header("🗑 Danh sách Spam / Từ chối")

  if st.text_input("🔐 Mã PIN Quản trị", type="password") != STAFF_PIN:
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
    if st.button("🔥 Xóa sạch tất cả Spam", type="primary"):
      conn = get_conn()
      for r in rows:
        if r["image_path"] and os.path.isfile(r["image_path"]):
          os.remove(r["image_path"])
      conn.execute("DELETE FROM reports WHERE status = 'Spam/Từ chối'")
      conn.commit()
      conn.close()
      st.success("Đã xóa sạch bộ nhớ Spam!")
      st.rerun()

    for r in rows:
      with st.expander(f"🚫 Spam #{r['id']} — {r['location']}"):
        st.write(f"📍 **Vị trí:** {r['location']}")
        st.error(r["ai_result"])

# ------------------------------------------------------------
# 5. CÀI ĐẶT AI
# ------------------------------------------------------------
elif menu == "⚙️ Cài đặt AI":
  st.header("⚙️ Cấu hình Cloudflare AI")

  if st.text_input("🔐 Mã PIN Cấu hình", type="password") != STAFF_PIN:
    st.warning("Nhập mã PIN để vào cài đặt.")
    st.stop()

  if st.button("☁️️ Kích hoạt Meta License AI", type="primary"):
    ok, msg = agree_to_meta_license()
    st.success(msg) if ok else st.error(msg)
