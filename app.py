import os
import json
import base64
import time
import sqlite3
import requests
from pathlib import Path
import streamlit as st
from PIL import Image
import folium
from streamlit_folium import st_folium
from google import genai
from pydantic import BaseModel, Field

# =========================================================
# 1. CẤU HÌNH TRANG & CSS
# =========================================================
st.set_page_config(
    page_title="Urban GreenEye AI - Mắt Xanh Đô Thị",
    page_icon="🌱",
    layout="wide"
)

st.markdown("""
    <style>
    .stApp { background-color: #f4f8f5 !important; font-family: 'Segoe UI', Roboto, sans-serif; }
    [data-testid="stSidebar"] { display: none; }
    
    .stApp p, .stApp label, .stApp span, .stApp div { color: #1b5e20 !important; }
    .stMarkdown h1, .stMarkdown h2, .stMarkdown h3, .stMarkdown h4 {
        color: #1b5e20 !important; font-weight: 700 !important;
    }

    .header-banner {
        background: linear-gradient(135deg, #1b5e20 0%, #2e7d32 60%, #4caf50 100%);
        padding: 22px 20px; border-radius: 16px; color: white !important;
        text-align: center; margin-bottom: 25px;
        box-shadow: 0 6px 20px rgba(46, 125, 50, 0.18);
    }
    .header-banner h1, .header-banner p { color: white !important; }
    
    .role-card {
        background: white; padding: 22px 20px; border-radius: 16px;
        border: 2px solid #c8e6c9; text-align: center;
    }
    
    .kpi-card {
        background: white; padding: 16px; border-radius: 12px;
        text-align: center; box-shadow: 0 2px 8px rgba(0,0,0,0.04);
        border-left: 5px solid #2e7d32;
    }
    .kpi-number { font-size: 1.8rem; font-weight: 800; color: #2e7d32 !important; }
    .kpi-label { font-size: 0.88rem; color: #555 !important; font-weight: 600; }

    .stTabs [data-baseweb="tab-list"] { gap: 12px !important; background-color: transparent !important; }
    .stTabs [data-baseweb="tab"] {
        height: auto !important; background-color: #ffffff !important;
        border-radius: 10px !important; padding: 12px 24px !important;
        font-weight: 700 !important; color: #2e7d32 !important;
        border: 1px solid #c8e6c9 !important;
    }
    .stTabs [aria-selected="true"] { background-color: #2e7d32 !important; color: #ffffff !important; }
    .stTabs [aria-selected="true"] p { color: #ffffff !important; }
    .stTabs [aria-selected="false"] p { color: #2e7d32 !important; }
    
    .leaderboard-card {
        background: #ffffff; padding: 15px; border-radius: 12px;
        border-left: 6px solid #fbc02d; box-shadow: 0 2px 8px rgba(0,0,0,0.05);
        margin-bottom: 10px;
    }
    </style>
""", unsafe_allow_html=True)

# =========================================================
# 2. KHỞI TẠO API CLIENTS
# =========================================================
raw_gemini_key = st.secrets.get("GEMINI_API_KEY", "")
gemini_key = str(raw_gemini_key).strip().strip('"').strip("'")
openrouter_key = st.secrets.get("OPENROUTER_API_KEY", "")
primary_model_name = st.secrets.get("GEMINI_MODEL", "gemini-2.5-flash")

gemini_client = None
if gemini_key:
    try:
        gemini_client = genai.Client(api_key=gemini_key)
    except Exception as e:
        st.warning(f"⚠️️ Không thể khởi tạo Gemini Client: {e}")

UPLOAD_DIR = Path("uploaded_images")
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

# =========================================================
# 3. HÀM AI DỰ PHÒNG MIỄN PHÍ 100% (OPENROUTER FREE)
# =========================================================
def encode_image_to_base64(image_path):
    with open(image_path, "rb") as image_file:
        return base64.b64encode(image_file.read()).decode('utf-8')

def call_openrouter_free_vision_api(image_path, system_instruction, response_format_schema):
    base64_image = encode_image_to_base64(image_path)
    prompt = f"""
    {system_instruction}
    
    BẠN BẮT BUỘC TRẢ VỀ DẠNG JSON THEO ĐÚNG CẤU TRÚC SAU (KHÔNG DÙNG MARKDOWN):
    {json.dumps(response_format_schema, ensure_ascii=False)}
    """

    headers = {"Content-Type": "application/json"}
    if openrouter_key:
        headers["Authorization"] = f"Bearer {openrouter_key}"

    free_models = [
        "google/gemini-2.0-flash-lite-001:free",
        "meta-llama/llama-3.2-11b-vision-instruct:free",
        "qwen/qwen-2-vl-7b-instruct:free"
    ]

    for model in free_models:
        payload = {
            "model": model,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{base64_image}"}}
                    ]
                }
            ],
            "response_format": {"type": "json_object"},
            "temperature": 0.1
        }
        try:
            res = requests.post("https://openrouter.ai/api/v1/chat/completions", headers=headers, json=payload, timeout=25)
            if res.status_code == 200:
                res_json = res.json()
                content = res_json['choices'][0]['message']['content']
                clean_content = content.replace("```json", "").replace("```", "").strip()
                return json.loads(clean_content)
        except Exception:
            continue

    raise Exception("Không thể kết nối dịch vụ AI dự phòng miễn phí.")

def analyze_with_ai_fallback(image_path, prompt, pydantic_schema, json_example_dict):
    if gemini_client:
        fallback_models = ["gemini-2.5-flash", "gemini-1.5-flash", primary_model_name]
        models_to_try = list(dict.fromkeys([m for m in fallback_models if m]))
        
        pil_img = Image.open(image_path)
        for model in models_to_try:
            try:
                response = gemini_client.models.generate_content(
                    model=model,
                    contents=[pil_img, prompt],
                    config={
                        "response_mime_type": "application/json",
                        "response_schema": pydantic_schema
                    }
                )
                return json.loads(response.text), "Gemini AI"
            except Exception as e:
                err_str = str(e).lower()
                if any(kw in err_str for kw in ["429", "503", "quota", "resource_exhausted", "limit"]):
                    time.sleep(0.5)
                    continue
                else:
                    break

    try:
        st.toast("⚡ Gemini tạm bận. Đã tự động chuyển sang Free Vision AI...", icon="🔄")
        res_data = call_openrouter_free_vision_api(image_path, prompt, json_example_dict)
        return res_data, "Free Vision AI"
    except Exception as free_err:
        raise Exception(f"Lỗi xử lý AI: {free_err}")

# Schemas cấu trúc JSON cho Pydantic
class WasteAnalysisSchema(BaseModel):
    contains_waste: bool = Field(description="True nếu ảnh chứa rác thải ô nhiễm")
    is_waste_amount_sufficient: bool = Field(description="True nếu khối lượng rác đủ nhiều")
    rejection_reason: str = Field(description="Lý do từ chối nếu không hợp lệ")
    waste_type: str = Field(description="Loại rác phát hiện")
    severity: str = Field(description="Mức độ: Thấp, Trung bình, hoặc Nghiêm trọng")
    assigned_role: str = Field(description="'VOLUNTEER' (nhẹ) hoặc 'AUTHORITY' (nặng)")
    action_plan: str = Field(description="Phương án xử lý")

json_waste_example = {
    "contains_waste": True,
    "is_waste_amount_sufficient": True,
    "rejection_reason": "",
    "waste_type": "Rác nhựa, túi nilon",
    "severity": "Trung bình",
    "assigned_role": "VOLUNTEER",
    "action_plan": "Cần thu gom túi nilon và phân loại rác tái chế"
}

class VerificationSchema(BaseModel):
    is_cleaned: bool = Field(description="True nếu địa điểm đã dọn sạch > 70%")
    confidence_score: int = Field(description="Điểm % làm sạch (0-100)")
    ai_comment: str = Field(description="Nhận xét chi tiết")
    earned_points: int = Field(description="Điểm cộng (10-50) nếu đạt trên 70%")

json_verify_example = {
    "is_cleaned": True,
    "confidence_score": 85,
    "ai_comment": "Khu vực đã được dọn dẹp sạch rác thải nhựa ban đầu.",
    "earned_points": 30
}

# =========================================================
# 4. CƠ SỞ DỮ LIỆU SQLITE
# =========================================================
DB_FILE = "reports.db"

def init_db():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute('''
        CREATE TABLE IF NOT EXISTS reports (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            location_name TEXT,
            latitude REAL,
            longitude REAL,
            description TEXT,
            image_path TEXT,
            waste_type TEXT,
            severity TEXT,
            assigned_role TEXT,
            action_plan TEXT,
            status TEXT DEFAULT 'Chờ xử lý',
            cleaned_image_path TEXT,
            verification_note TEXT,
            cleaner_team TEXT,
            points_earned INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    conn.commit()
    conn.close()

init_db()

def save_report(location_name, lat, lng, description, image_path, waste_type, severity, assigned_role, action_plan):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute(
        "INSERT INTO reports (location_name, latitude, longitude, description, image_path, waste_type, severity, assigned_role, action_plan) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (location_name, lat, lng, description, image_path, waste_type, severity, assigned_role, action_plan)
    )
    conn.commit()
    conn.close()

def get_all_reports():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT id, location_name, latitude, longitude, description, image_path, waste_type, severity, assigned_role, action_plan, status, cleaned_image_path, verification_note, cleaner_team, points_earned, created_at FROM reports ORDER BY id DESC")
    rows = c.fetchall()
    conn.close()
    return rows

def update_resolution(report_id, cleaned_image_path, verification_note, cleaner_team, points):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("UPDATE reports SET status = 'Đã hoàn thành', cleaned_image_path = ?, verification_note = ?, cleaner_team = ?, points_earned = ? WHERE id = ?", 
              (cleaned_image_path, verification_note, cleaner_team, points, report_id))
    conn.commit()
    conn.close()

def get_leaderboard():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT cleaner_team, SUM(points_earned) as total_points, COUNT(id) as tasks_completed FROM reports WHERE status = 'Đã hoàn thành' AND cleaner_team IS NOT NULL GROUP BY cleaner_team ORDER BY total_points DESC")
    rows = c.fetchall()
    conn.close()
    return rows

def clear_all_history():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("DELETE FROM reports")
    c.execute("DELETE FROM sqlite_sequence WHERE name='reports'")
    conn.commit()
    conn.close()

# =========================================================
# 5. GIAO DIỆN CHÍNH
# =========================================================
if "user_role" not in st.session_state:
    st.session_state.user_role = None

STAFF_PIN = "1234"

st.markdown("""
    <div class="header-banner">
        <h1>🌱 URBAN GREENEYE AI – MẮT XANH ĐÔ THỊ</h1>
        <p>Hệ Thống Phản Ánh Môi Trường Tích Hợp AI Miễn Phí</p>
    </div>
""", unsafe_allow_html=True)

if st.session_state.user_role is None:
    st.markdown("<h3 style='text-align: center; color: #1b5e20;'>👋 CHỌN VAI TRÒ TRUY CẬP HỆ THỐNG</h3>", unsafe_allow_html=True)
    st.write("")
    
    _, col1, col2, _ = st.columns([0.3, 2, 2, 0.3], gap="large")
    
    with col1:
        st.markdown("""
        <div class="role-card">
            <div style="font-size:3rem; margin-bottom:10px;">👤</div>
            <h3 style="color: #2e7d32;">NGƯỜI DÂN PHẢN ÁNH</h3>
            <p style="color: #666; font-size: 0.92rem;">Gửi báo cáo rác thải, xem danh sách điểm đã dọn dẹp & bản đồ.</p>
        </div>
        """, unsafe_allow_html=True)
        st.write("")
        if st.button("👉 Vào Giao Diện Người Dân", type="primary", use_container_width=True):
            st.session_state.user_role = "CITIZEN"
            st.rerun()

    with col2:
        st.markdown("""
        <div class="role-card">
            <div style="font-size:3rem; margin-bottom:10px;">🧹🏛️</div>
            <h3 style="color: #1b5e20;">ĐỘI TÌNH NGUYỆN & CƠ QUAN</h3>
            <p style="color: #666; font-size: 0.92rem;">Xem danh sách, dọn dẹp, tải ảnh tích điểm vinh danh bằng AI.</p>
        </div>
        """, unsafe_allow_html=True)
        staff_pin = st.text_input("🔑 Mã bảo mật (PIN)", type="password", key="pin_staff", placeholder="Nhập PIN...")
        if st.button("🔓 Đăng Nhập Dashboard Quản Lý", use_container_width=True):
            if staff_pin == STAFF_PIN:
                st.session_state.user_role = "STAFF"
                st.rerun()
            else:
                st.error("❌ Mã PIN chưa đúng! (Mã mặc định: 1234)")

else:
    col_nav1, col_nav2 = st.columns([3.5, 1.2])
    with col_nav1:
        role_badge = "🟢 Giao diện Người Dân Phản Ánh" if st.session_state.user_role == "CITIZEN" else "🛡️ Dashboard Đội Tình Nguyện & Cơ Quan"
        st.markdown(f"<h3 style='color: #1b5e20; margin:0;'>{role_badge}</h3>", unsafe_allow_html=True)
    with col_nav2:
        if st.button("🚪 Đổi vai trò / Đăng xuất", use_container_width=True):
            st.session_state.user_role = None
            st.rerun()

    st.markdown("---")

    if st.session_state.user_role == "CITIZEN":
        tab_c1, tab_c2, tab_c3 = st.tabs(["📝 Gửi Báo Cáo Phản Ánh", "✅ Danh Sách Đã Dọn Dẹp", "🗺️ Bản Đồ Môi Trường"])
        
        with tab_c1:
            col1, col2 = st.columns([1, 1], gap="large")
            with col1:
                st.markdown("#### 📍 1. Chọn vị trí & Điền thông tin")
                default_lat, default_lng = 10.7769, 106.7009
                m = folium.Map(location=[default_lat, default_lng], zoom_start=13, tiles="OpenStreetMap")
                map_data = st_folium(m, height=270, width="100%", key="input_map")
                selected_lat, selected_lng = default_lat, default_lng
                if map_data and map_data.get("last_clicked"):
                    selected_lat = map_data["last_clicked"]["lat"]
                    selected_lng = map_data["last_clicked"]["lng"]
                    st.success(f"🎯 Đã ghim tọa độ: `{selected_lat:.5f}, {selected_lng:.5f}`")

                location_name = st.text_input("Tên địa điểm / Con đường", placeholder="Ví dụ: Công viên 23/9, Quận 1")
                description = st.text_area("Mô tả thêm (Tùy chọn)", placeholder="Mô tả hiện trạng rác thải...")
                uploaded_file = st.file_uploader("📸 2. Tải ảnh thực tế điểm ô nhiễm *", type=["jpg", "jpeg", "png"])
                
                if uploaded_file:
                    image = Image.open(uploaded_file)
                    st.image(image, caption="Ảnh thực tế đã tải", use_container_width=True)

            with col2:
                st.markdown("#### 🤖 3. AI Phân Loại & Anti-Spam Tự Động")
                st.info("Hệ thống kiểm duyệt ảnh tự động và thông minh.")
                
                if st.button("🚀 GỬI BÁO CÁO NGAY", type="primary", use_container_width=True):
                    if not uploaded_file:
                        st.error("⚠️ Vui lòng tải ảnh thực tế điểm ô nhiễm!")
                    else:
                        with st.spinner("🤖 AI đang phân tích ảnh & kiểm tra anti-spam..."):
                            try:
                                save_path = UPLOAD_DIR / uploaded_file.name
                                with open(save_path, "wb") as f:
                                    f.write(uploaded_file.getbuffer())

                                prompt = (
                                    "Phân tích bức ảnh này để báo cáo môi trường:\n"
                                    "1. KIỂM TRA RÁC: Bức ảnh có thực sự chứa rác thải gây ô nhiễm không? (Nếu là ảnh selfie, mặt người, cảnh vật sạch, động vật... -> contains_waste = False).\n"
                                    "2. LƯỢNG RÁC: Khối lượng rác có đủ lớn để tạo thành bãi ô nhiễm không? (Nếu chỉ có 1-2 mẩu rác rất nhỏ -> is_waste_amount_sufficient = False).\n"
                                    "3. Phân loại waste_type, severity (Thấp/Trung bình/Nghiêm trọng), và assigned_role: Rác nhẹ -> 'VOLUNTEER', Rác nặng/nguy hại -> 'AUTHORITY'."
                                )
                                
                                data, ai_engine = analyze_with_ai_fallback(
                                    image_path=save_path,
                                    prompt=prompt,
                                    pydantic_schema=WasteAnalysisSchema,
                                    json_example_dict=json_waste_example
                                )
                                
                                if not data.get("contains_waste", False):
                                    st.error("❌ BÁO CÁO BỊ TỪ CHỐI (ẢNH KHÔNG HỢP LỆ)!")
                                    st.warning(f"🤖 **Phản hồi từ {ai_engine}:** {data.get('rejection_reason', 'Ảnh tải lên không phát hiện rác thải ô nhiễm.')}")
                                elif not data.get("is_waste_amount_sufficient", False):
                                    st.error("❌ BÁO CÁO BỊ TỪ CHỐI (LƯỢNG RÁC KHÔNG ĐỦ NGƯỠNG)!")
                                    st.warning(f"🤖 **Phản hồi từ {ai_engine}:** {data.get('rejection_reason', 'Khối lượng rác quá nhỏ không đủ cấu thành ô nhiễm.')}")
                                else:
                                    final_loc = location_name if location_name else f"Tọa độ ({selected_lat:.4f}, {selected_lng:.4f})"
                                    save_report(final_loc, selected_lat, selected_lng, description, str(save_path), data.get("waste_type","Rác sinh hoạt"), data.get("severity","Trung bình"), data.get("assigned_role","VOLUNTEER"), data.get("action_plan","Thu gom rác"))
                                    st.balloons()
                                    st.success(f"🎉 Báo cáo hợp lệ! {ai_engine} đã ghi nhận và chuyển tới đơn vị xử lý.")
                            except Exception as e:
                                st.error(f"Lỗi AI: {e}")

        with tab_c2:
            st.markdown("#### ✅ Danh sách các điểm ô nhiễm đã được xử lý làm sạch thành công")
            all_reports = get_all_reports()
            cleaned_reports = [r for r in all_reports if r[10] == "Đã hoàn thành"]
            
            if not cleaned_reports:
                st.info("🌱 Hiện chưa có địa điểm nào hoàn tất dọn dẹp. Các báo cáo đang được xử lý!")
            else:
                for r in cleaned_reports:
                    r_id, r_loc, r_lat, r_lng, r_desc, r_img, r_type, r_sev, r_role, r_plan, r_status, r_clean_img, r_ver_note, r_team, r_points, r_time = r
                    with st.expander(f"✨ [ĐÃ DỌN SẠCH] {r_loc} — Thực hiện bởi: {r_team} (+{r_points} điểm)"):
                        col_before, col_after = st.columns(2)
                        with col_before:
                            st.markdown("##### 🔴 Hiện trạng rác ban đầu:")
                            if os.path.exists(r_img): st.image(r_img, use_container_width=True)
                            st.caption(f"**Loại rác:** {r_type} | **Mức độ:** {r_sev}")
                        with col_after:
                            st.markdown("##### 🟢 Kết quả sau khi làm sạch:")
                            if r_clean_img and os.path.exists(r_clean_img): st.image(r_clean_img, use_container_width=True)
                            st.success(f"🏆 **Đơn vị thực hiện:** {r_team}")
                            st.info(f"🤖 **AI Chấm Điểm Thẩm Định:**\n{r_ver_note}")

        with tab_c3:
            st.markdown("#### 🗺️ Bản đồ các điểm ô nhiễm cộng đồng")
            reports = get_all_reports()
            if reports:
                m_all = folium.Map(location=[reports[0][2], reports[0][3]], zoom_start=12, tiles="OpenStreetMap")
                for r in reports:
                    color = "green" if r[10] == "Đã hoàn thành" else "red"
                    folium.Marker([r[2], r[3]], popup=f"<b>{r[1]}</b><br>Trạng thái: {r[10]}", icon=folium.Icon(color=color, icon="leaf")).add_to(m_all)
                st_folium(m_all, height=450, width="100%", key="cit_map")

    elif st.session_state.user_role == "STAFF":
        reports = get_all_reports()
        total_reports = len(reports)
        pending_reports = sum(1 for r in reports if r[10] != "Đã hoàn thành")
        completed_reports = sum(1 for r in reports if r[10] == "Đã hoàn thành")
        success_rate = int((completed_reports / total_reports * 100)) if total_reports > 0 else 0

        k1, k2, k3, k4 = st.columns(4)
        with k1: st.markdown(f'<div class="kpi-card"><div class="kpi-number">{total_reports}</div><div class="kpi-label">Tổng phản ánh</div></div>', unsafe_allow_html=True)
        with k2: st.markdown(f'<div class="kpi-card" style="border-left-color:#e53935;"><div class="kpi-number" style="color:#e53935;">{pending_reports}</div><div class="kpi-label">🔴 Cần dọn dẹp</div></div>', unsafe_allow_html=True)
        with k3: st.markdown(f'<div class="kpi-card" style="border-left-color:#43a047;"><div class="kpi-number" style="color:#43a047;">{completed_reports}</div><div class="kpi-label">🟢 Đã hoàn thành</div></div>', unsafe_allow_html=True)
        with k4: st.markdown(f'<div class="kpi-card" style="border-left-color:#1e88e5;"><div class="kpi-number" style="color:#1e88e5;">{success_rate}%</div><div class="kpi-label">⚡ Tỷ lệ hoàn thành</div></div>', unsafe_allow_html=True)
        
        st.write("")

        with st.expander("⚙️ Quản trị hệ thống & Xóa lịch sử dữ liệu"):
            st.warning("⚠️ Hành động này sẽ xóa vĩnh viễn toàn bộ danh sách phản ánh, lịch sử dọn dẹp và điểm vinh danh!")
            if st.button("🗑 XÓA SẠCH LỊCH SỬ BÁO CÁO (RESET SYSTEM)", type="primary"):
                clear_all_history()
                st.success("✅ Đã xóa toàn bộ lịch sử dữ liệu thành công!")
                st.rerun()

        tab_s1, tab_s2, tab_s3 = st.tabs(["📋 Báo Cáo & AI Thẩm Định (>70%)", "🏆 Bảng Vàng Vinh Danh", "🗺️ Bản Đồ Sự Cố"])
        
        with tab_s1:
            if not reports:
                st.info("🎉 Hiện tại không có báo cáo ô nhiễm nào cần xử lý.")
            else:
                for r in reports:
                    r_id, r_loc, r_lat, r_lng, r_desc, r_img, r_type, r_sev, r_role, r_plan, r_status, r_clean_img, r_ver_note, r_team, r_points, r_time = r
                    status_badge = "🟢 Đã hoàn thành" if r_status == "Đã hoàn thành" else "🔴 Chờ xử lý"
                    role_label = "🧹 Đội Tình Nguyện" if r_role == "VOLUNTEER" else "🏛️ Cơ Quan Chức Năng"
                    
                    with st.expander(f"[{status_badge}] Nhiệm vụ #{r_id}: {r_loc} — ({role_label})"):
                        c1, c2 = st.columns([1, 1], gap="medium")
                        with c1:
                            st.markdown("##### 📸 Bằng chứng ô nhiễm ban đầu:")
                            if os.path.exists(r_img): st.image(r_img, use_container_width=True)
                            st.markdown(f"• **📍 Vị trí:** {r_loc}")
                            st.markdown(f"• **🏷️ Loại rác:** {r_type} | **Mức độ:** {r_sev}")
                            st.info(f"💡 **Phương án gợi ý:** {r_plan}")

                        with c2:
                            st.markdown("##### 🤖 XÁC MINH & TÍCH ĐIỂM VINH DANH")
                            if r_status == "Đã hoàn thành":
                                st.success(f"✅ Đã hoàn thành dọn dẹp bởi **{r_team}** (+{r_points} điểm vinh danh)")
                                if r_clean_img and os.path.exists(r_clean_img):
                                    st.image(r_clean_img, caption="Ảnh thực tế sau dọn dẹp", use_container_width=True)
                                st.markdown(f"**Nhận xét AI:**\n{r_ver_note}")
                            else:
                                team_name_input = st.text_input(f"🏷️ Tên Cá Nhân / Đội Dọn Dẹp (Mã #{r_id})", placeholder="VD: Đội Tình Nguyện Xanh 1", key=f"team_{r_id}")
                                clean_file = st.file_uploader(f"Tải ảnh đã dọn xong (Mã #{r_id})", type=["jpg", "png", "jpeg"], key=f"up_staff_{r_id}")
                                
                                if clean_file and st.button(f"🚀 AI Thẩm Định & Tích Điểm #{r_id}", type="primary"):
                                    if not team_name_input.strip():
                                        st.error("⚠️ Vui lòng nhập Tên Đội hoặc Cá Nhân dọn dẹp!")
                                    else:
                                        with st.spinner("🤖 AI đang đối chiếu hình ảnh..."):
                                            try:
                                                clean_save_path = UPLOAD_DIR / f"cleaned_{r_id}_{clean_file.name}"
                                                with open(clean_save_path, "wb") as f:
                                                    f.write(clean_file.getbuffer())

                                                verify_prompt = (
                                                    "So sánh bức ảnh dọn dẹp này với hiện trạng rác ban đầu:\n"
                                                    "1. Đánh giá tỷ lệ phần trăm dọn dẹp sạch sẽ (confidence_score từ 0 đến 100).\n"
                                                    "2. Only set is_cleaned = True khi tỷ lệ sạch đạt từ 70% trở lên.\n"
                                                    "3. Tích điểm earned_points (từ 10 đến 50 điểm) dựa trên lượng rác đã giải quyết nếu đạt >70%."
                                                )

                                                v_data, ai_engine = analyze_with_ai_fallback(
                                                    image_path=clean_save_path,
                                                    prompt=verify_prompt,
                                                    pydantic_schema=VerificationSchema,
                                                    json_example_dict=json_verify_example
                                                )
                                                
                                                score = v_data.get("confidence_score", 0)
                                                pts = v_data.get("earned_points", 0)
                                                is_clean = v_data.get("is_cleaned", False)
                                                
                                                if is_clean or score >= 70:
                                                    note = f"Thăng điểm dọn sạch {ai_engine} đánh giá: {score}/100\nNhận xét: {v_data.get('ai_comment','')}"
                                                    update_resolution(r_id, str(clean_save_path), note, team_name_input.strip(), pts)
                                                    st.balloons()
                                                    st.success(f"🎉 Chúc mừng **{team_name_input}**! {ai_engine} xác minh đạt {score}% và cộng +{pts} điểm!")
                                                    st.rerun()
                                                else:
                                                    st.warning(f"⚠️ {ai_engine} đánh giá kết quả chỉ đạt **{score}%** (Chưa đạt mốc 70%). Vui lòng dọn dẹp thêm!")
                                            except Exception as e:
                                                st.error(f"Lỗi AI: {e}")

        with tab_s2:
            st.markdown("### 🏆 BẢNG XẾP HẠNG TÌNH NGUYỆN VIÊN / CƠ QUAN XUẤT SẮC")
            leaderboard_data = get_leaderboard()
            if not leaderboard_data:
                st.info("Chưa có đội nào hoàn thành nhiệm vụ đạt chuẩn > 70%.")
            else:
                for idx, (team, total_pts, count) in enumerate(leaderboard_data, 1):
                    rank_icon = "🥇" if idx == 1 else ("🥈" if idx == 2 else ("🥉" if idx == 3 else f"#{idx}"))
                    st.markdown(f"""
                    <div class="leaderboard-card">
                        <div style="display: flex; justify-content: space-between; align-items: center;">
                            <div>
                                <h4 style="margin:0; color:#1b5e20;">{rank_icon} {team}</h4>
                                <span style="font-size:0.88rem; color:#666;">Đã hoàn thành: <b>{count} nhiệm vụ</b></span>
                            </div>
                            <div style="text-align:right;">
                                <span style="font-size:1.4rem; font-weight:800; color:#d81b60;">+{total_pts}</span>
                                <span style="font-size:0.85rem; color:#888;"> điểm</span>
                            </div>
                        </div>
                    </div>
                    """, unsafe_allow_html=True)

        with tab_s3:
            st.markdown("#### 🗺️ Bản đồ quản lý sự cố")
            if reports:
                m_staff = folium.Map(location=[reports[0][2], reports[0][3]], zoom_start=12, tiles="OpenStreetMap")
                for r in reports:
                    color = "green" if r[10] == "Đã hoàn thành" else ("orange" if r[8] == "VOLUNTEER" else "red")
                    folium.Marker([r[2], r[3]], popup=f"<b>{r[1]}</b><br>Trạng thái: {r[10]}", icon=folium.Icon(color=color, icon="leaf")).add_to(m_staff)
                st_folium(m_staff, height=450, width="100%", key="staff_map")
