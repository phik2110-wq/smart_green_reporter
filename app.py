import os
import json
import sqlite3
from pathlib import Path
import streamlit as st
from PIL import Image
import folium
from streamlit_folium import st_folium
from google import genai
from pydantic import BaseModel, Field

# =========================================================
# 1. CẤU HÌNH TRANG & CSS TÙY BIẾN SANG TRỌNG
# =========================================================
st.set_page_config(
    page_title="Smart Green Reporter - Quản Lý Môi Trường",
    page_icon="🌱",
    layout="wide"
)

st.markdown("""
    <style>
    /* Nền tổng thể nhẹ nhàng */
    .stApp {
        background-color: #f2f7f4;
        font-family: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif;
    }
    
    /* Ẩn Sidebar mặc định */
    [data-testid="stSidebar"] { display: none; }
    
    /* Header Banner Xanh Môi Trường */
    .header-banner {
        background: linear-gradient(135deg, #1b5e20 0%, #388e3c 50%, #66bb6a 100%);
        padding: 28px 20px;
        border-radius: 18px;
        color: white;
        text-align: center;
        margin-bottom: 25px;
        box-shadow: 0 8px 24px rgba(46, 125, 50, 0.22);
    }
    .header-banner h1 {
        color: white !important;
        font-weight: 800;
        letter-spacing: 0.5px;
        margin-bottom: 6px;
        text-shadow: 0 2px 4px rgba(0,0,0,0.2);
    }
    .header-banner p {
        font-size: 1.15rem;
        opacity: 0.95;
        margin: 0;
    }

    /* Thẻ Chọn Vai Trò */
    .role-card {
        background: white;
        padding: 28px 22px;
        border-radius: 18px;
        border: 2px solid #e0f2f1;
        box-shadow: 0 6px 20px rgba(0,0,0,0.04);
        text-align: center;
        transition: all 0.3s ease;
    }
    .role-card:hover {
        border-color: #2e7d32;
        box-shadow: 0 10px 25px rgba(46, 125, 50, 0.12);
        transform: translateY(-3px);
    }
    .role-icon {
        font-size: 3.2rem;
        margin-bottom: 12px;
    }

    /* Thẻ Thống Kê KPI */
    .kpi-card {
        background: white;
        padding: 16px;
        border-radius: 14px;
        text-align: center;
        box-shadow: 0 3px 10px rgba(0,0,0,0.03);
        border-left: 5px solid #2e7d32;
    }
    .kpi-number {
        font-size: 1.8rem;
        font-weight: 800;
        color: #2e7d32;
    }
    .kpi-label {
        font-size: 0.88rem;
        color: #555;
        font-weight: 600;
    }

    /* Style Tab đẹp mắt */
    .stTabs [data-baseweb="tab-list"] {
        gap: 10px;
    }
    .stTabs [data-baseweb="tab"] {
        height: 46px;
        background-color: #ffffff;
        border-radius: 10px;
        padding: 0px 22px;
        box-shadow: 0 2px 6px rgba(0,0,0,0.03);
        font-weight: 600;
        color: #2e7d32;
    }
    .stTabs [aria-selected="true"] {
        background-color: #2e7d32 !important;
        color: white !important;
    }
    </style>
""", unsafe_allow_html=True)

# Lấy Secrets
api_key = st.secrets.get("GEMINI_API_KEY")
model_name = st.secrets.get("GEMINI_MODEL", "gemini-2.5-flash")

if not api_key:
    st.error("⚠️ Chưa cấu hình GEMINI_API_KEY trong Secrets trên Streamlit Cloud!")
    st.stop()

client = genai.Client(api_key=api_key)

UPLOAD_DIR = Path("uploaded_images")
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

# Schema phân tích rác thải
class WasteAnalysisSchema(BaseModel):
    waste_type: str = Field(description="Các loại rác phát hiện trong hình")
    severity: str = Field(description="Mức độ ô nhiễm: Thấp, Trung bình, hoặc Nghiêm trọng")
    assigned_role: str = Field(description="Chỉ điền 'VOLUNTEER' (Tình nguyện) hoặc 'AUTHORITY' (Cơ quan)")
    action_plan: str = Field(description="Phương án xử lý chi tiết")

# Schema kiểm chứng dọn dẹp bằng AI
class VerificationSchema(BaseModel):
    is_cleaned: bool = Field(description="True nếu địa điểm đã sạch rác hoặc được dọn dẹp tốt, False nếu vẫn còn rác thải")
    confidence_score: int = Field(description="Thang điểm từ 0 đến 100 đánh giá mức độ sạch")
    ai_comment: str = Field(description="Nhận xét chi tiết của AI về kết quả dọn dẹp")

# =========================================================
# 2. CƠ SỞ DỮ LIỆU SQLITE & MIGRATION TỰ ĐỘNG (SỬA LỖI TRIỆT ĐỂ)
# =========================================================
DB_FILE = "reports.db"

def init_db():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    # 1. Tạo bảng nếu chưa có
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
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    # 2. Tự động thêm cột nếu DB cũ bị thiếu (Tránh lỗi no such column)
    columns_to_check = [
        ("waste_type", "TEXT"),
        ("severity", "TEXT"),
        ("assigned_role", "TEXT"),
        ("action_plan", "TEXT"),
        ("status", "TEXT DEFAULT 'Chờ xử lý'"),
        ("cleaned_image_path", "TEXT"),
        ("verification_note", "TEXT")
    ]
    for col_name, col_type in columns_to_check:
        try:
            c.execute(f"ALTER TABLE reports ADD COLUMN {col_name} {col_type}")
        except sqlite3.OperationalError:
            pass # Cột đã tồn tại
            
    conn.commit()
    conn.close()

# Khởi chạy & cập nhật DB ngay lập tức
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
    c.execute("SELECT id, location_name, latitude, longitude, description, image_path, waste_type, severity, assigned_role, action_plan, status, cleaned_image_path, verification_note, created_at FROM reports ORDER BY id DESC")
    rows = c.fetchall()
    conn.close()
    return rows

def update_resolution(report_id, cleaned_image_path, verification_note):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("UPDATE reports SET status = 'Đã hoàn thành', cleaned_image_path = ?, verification_note = ? WHERE id = ?", (cleaned_image_path, verification_note, report_id))
    conn.commit()
    conn.close()

# =========================================================
# 3. QUẢN LÝ SESSION & BANNER
# =========================================================
if "user_role" not in st.session_state:
    st.session_state.user_role = None

STAFF_PIN = "1234"

st.markdown("""
    <div class="header-banner">
        <h1>🌱 SMART GREEN REPORTER</h1>
        <p>Hệ Thống Phản Ánh, Điều Phối & AI Xác Minh Môi Trường Thông Minh</p>
    </div>
""", unsafe_allow_html=True)

# =========================================================
# MÀN HÌNH CHỌN VAI TRÒ (NẾU CHƯA ĐĂNG NHẬP)
# =========================================================
if st.session_state.user_role is None:
    st.markdown("<h3 style='text-align: center; color: #1b5e20; font-weight:700;'>👋 CHỌN VAI TRÒ TRUY CẬP HỆ THỐNG</h3>", unsafe_allow_html=True)
    st.write("")
    
    _, col1, col2, _ = st.columns([0.3, 2, 2, 0.3], gap="large")
    
    with col1:
        st.markdown("""
        <div class="role-card">
            <div class="role-icon">👤</div>
            <h3 style="color: #2e7d32; margin-bottom:8px;">NGƯỜI DÂN PHẢN ÁNH</h3>
            <p style="color: #666; font-size: 0.92rem;">Gửi báo cáo ô nhiễm kèm hình ảnh thực tế và ghim tọa độ trực tiếp trên bản đồ.</p>
        </div>
        """, unsafe_allow_html=True)
        st.write("")
        if st.button("👉 Vào Giao Diện Người Dân", type="primary", use_container_width=True):
            st.session_state.user_role = "CITIZEN"
            st.rerun()

    with col2:
        st.markdown("""
        <div class="role-card">
            <div class="role-icon">🧹🏛️</div>
            <h3 style="color: #1b5e20; margin-bottom:8px;">ĐỘI TÌNH NGUYỆN & CƠ QUAN</h3>
            <p style="color: #666; font-size: 0.92rem;">Dashboard theo dõi phản ánh, nhận nhiệm vụ và tải ảnh dọn dẹp để AI kiểm chứng.</p>
        </div>
        """, unsafe_allow_html=True)
        staff_pin = st.text_input("🔑 Mã bảo mật (PIN)", type="password", key="pin_staff", placeholder="Nhập PIN...")
        if st.button("🔓 Đăng Nhập Dashboard Quản Lý", use_container_width=True):
            if staff_pin == STAFF_PIN:
                st.session_state.user_role = "STAFF"
                st.rerun()
            else:
                st.error("❌ Mã PIN chưa đúng! (Mã thử nghiệm: 1234)")

# =========================================================
# GIAO DIỆN CHÍNH (SAU KHI ĐĂNG NHẬP)
# =========================================================
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

    # ---------------------------------------------------------
    # 1. GIAO DIỆN NGƯỜI DÂN
    # ---------------------------------------------------------
    if st.session_state.user_role == "CITIZEN":
        tab_c1, tab_c2 = st.tabs(["📝 Gửi Báo Cáo Phản Ánh", "🗺️ Bản Đồ Môi Trường Cộng Đồng"])
        
        with tab_c1:
            col1, col2 = st.columns([1, 1], gap="large")
            with col1:
                st.markdown("#### 📍 1. Chọn vị trí & Điền thông tin")
                default_lat, default_lng = 10.7769, 106.7009
                m = folium.Map(location=[default_lat, default_lng], zoom_start=13, tiles="CartoDB positron")
                folium.TileLayer('OpenStreetMap').add_to(m)
                
                map_data = st_folium(m, height=270, width="100%", key="input_map")
                selected_lat, selected_lng = default_lat, default_lng
                if map_data and map_data.get("last_clicked"):
                    selected_lat = map_data["last_clicked"]["lat"]
                    selected_lng = map_data["last_clicked"]["lng"]
                    st.success(f"🎯 Đã ghim tọa độ: `{selected_lat:.5f}, {selected_lng:.5f}`")

                location_name = st.text_input("Tên địa điểm / Con đường", placeholder="Ví dụ: Công viên 23/9, Quận 1")
                description = st.text_area("Mô tả thêm (Tùy chọn)", placeholder="Mô tả hiện trạng rác thải, mùi hôi...")
                uploaded_file = st.file_uploader("📸 2. Tải ảnh thực tế điểm ô nhiễm *", type=["jpg", "jpeg", "png"])
                
                if uploaded_file:
                    image = Image.open(uploaded_file)
                    st.image(image, caption="Hình ảnh thực tế bạn đã tải", use_container_width=True)

            with col2:
                st.markdown("#### 🤖 3. AI Tự Động Phân Loại & Phân Luồng")
                st.info("Hệ thống AI sẽ tự động phân tích hình ảnh, xác định loại rác và đề xuất phương án xử lý ngay lập tức.")
                
                if st.button("🚀 GỬI BÁO CÁO NGAY", type="primary", use_container_width=True):
                    if not uploaded_file:
                        st.error("⚠️ Vui lòng tải ảnh thực tế điểm ô nhiễm!")
                    else:
                        with st.spinner("🤖 AI Gemini đang phân tích hình ảnh..."):
                            try:
                                save_path = UPLOAD_DIR / uploaded_file.name
                                with open(save_path, "wb") as f:
                                    f.write(uploaded_file.getbuffer())

                                prompt = (
                                    "Phân tích ảnh rác thải này:\n"
                                    "1. Rác nhẹ, túi nilon, đồ nhựa, quy mô nhỏ -> assigned_role = 'VOLUNTEER'.\n"
                                    "2. Rác nguy hại, ô nhiễm diện rộng, nguồn nước, cồng kềnh -> assigned_role = 'AUTHORITY'."
                                )
                                
                                response = client.models.generate_content(
                                    model=model_name,
                                    contents=[image, prompt],
                                    config={
                                        "response_mime_type": "application/json",
                                        "response_schema": WasteAnalysisSchema,
                                    }
                                )
                                data = json.loads(response.text)
                                final_loc = location_name if location_name else f"Tọa độ ({selected_lat:.4f}, {selected_lng:.4f})"
                                
                                save_report(
                                    final_loc, selected_lat, selected_lng, description, str(save_path),
                                    data["waste_type"], data["severity"], data["assigned_role"], data["action_plan"]
                                )
                                st.balloons()
                                st.success("🎉 Gửi phản ánh thành công! Báo cáo đã được ghi nhận.")
                                st.markdown(f"• **Loại rác:** {data['waste_type']}")
                                st.markdown(f"• **Mức độ:** {data['severity']}")
                                st.markdown(f"• **Đơn vị tiếp nhận:** {'Đội Tình Nguyện' if data['assigned_role'] == 'VOLUNTEER' else 'Cơ Quan Chức Năng'}")
                            except Exception as e:
                                st.error(f"Lỗi phân tích AI: {e}")

        with tab_c2:
            st.markdown("#### 🗺️ Bản đồ các điểm ô nhiễm cộng đồng")
            reports = get_all_reports()
            if reports:
                m_all = folium.Map(location=[reports[0][2], reports[0][3]], zoom_start=12, tiles="CartoDB positron")
                for r in reports:
                    color = "green" if r[10] == "Đã hoàn thành" else "red"
                    folium.Marker(
                        [r[2], r[3]],
                        popup=f"<b>{r[1]}</b><br>Trạng thái: {r[10]}",
                        tooltip=f"{r[1]} ({r[10]})",
                        icon=folium.Icon(color=color, icon="leaf")
                    ).add_to(m_all)
                st_folium(m_all, height=450, width="100%", key="cit_map")

    # ---------------------------------------------------------
    # 2. DASHBOARD ĐỘI TÌNH NGUYỆN & CƠ QUAN
    # ---------------------------------------------------------
    elif st.session_state.user_role == "STAFF":
        reports = get_all_reports()
        
        # Thống kê KPI
        total_reports = len(reports)
        pending_reports = sum(1 for r in reports if r[10] != "Đã hoàn thành")
        completed_reports = sum(1 for r in reports if r[10] == "Đã hoàn thành")
        success_rate = int((completed_reports / total_reports * 100)) if total_reports > 0 else 0

        k1, k2, k3, k4 = st.columns(4)
        with k1:
            st.markdown(f'<div class="kpi-card"><div class="kpi-number">{total_reports}</div><div class="kpi-label">Tổng phản ánh</div></div>', unsafe_allow_html=True)
        with k2:
            st.markdown(f'<div class="kpi-card" style="border-left-color:#e53935;"><div class="kpi-number" style="color:#e53935;">{pending_reports}</div><div class="kpi-label">🔴 Cần dọn dẹp</div></div>', unsafe_allow_html=True)
        with k3:
            st.markdown(f'<div class="kpi-card" style="border-left-color:#43a047;"><div class="kpi-number" style="color:#43a047;">{completed_reports}</div><div class="kpi-label">🟢 Đã hoàn thành</div></div>', unsafe_allow_html=True)
        with k4:
            st.markdown(f'<div class="kpi-card" style="border-left-color:#1e88e5;"><div class="kpi-number" style="color:#1e88e5;">{success_rate}%</div><div class="kpi-label">⚡ Tỷ lệ khắc phục</div></div>', unsafe_allow_html=True)
        
        st.write("")

        tab_s1, tab_s2 = st.tabs(["📋 Danh Sách Báo Cáo & AI Kiểm Chứng", "🗺️ Bản Đồ Vị Trí Sự Cố"])
        
        with tab_s1:
            if not reports:
                st.info("🎉 Hiện chưa có điểm ô nhiễm nào cần xử lý.")
            else:
                for r in reports:
                    r_id, r_loc, r_lat, r_lng, r_desc, r_img, r_type, r_sev, r_role, r_plan, r_status, r_clean_img, r_ver_note, r_time = r
                    
                    status_badge = "🟢 Đã hoàn thành" if r_status == "Đã hoàn thành" else "🔴 Chờ xử lý"
                    role_label = "🧹 Đội Tình Nguyện" if r_role == "VOLUNTEER" else "🏛️ Cơ Quan Chức Năng"
                    
                    with st.expander(f"[{status_badge}] Nhiệm vụ #{r_id}: {r_loc} — (Phân luồng: {role_label})"):
                        c1, c2 = st.columns([1, 1], gap="medium")
                        
                        # Cột thông tin ban đầu
                        with c1:
                            st.markdown("##### 📸 Ảnh bằng chứng ô nhiễm ban đầu:")
                            if os.path.exists(r_img):
                                st.image(r_img, use_container_width=True)
                            
                            st.markdown(f"• **📍 Vị trí:** {r_loc} (`{r_lat:.5f}, {r_lng:.5f}`)")
                            st.markdown(f"• **🏷️ Loại rác:** {r_type}")
                            st.markdown(f"• **⚠️ Mức độ:** {r_sev}")
                            st.markdown(f"• **📝 Mô tả từ dân:** {r_desc if r_desc else 'Không có'}")
                            st.info(f"💡 **Phương án gợi ý từ AI:**\n{r_plan}")

                        # Cột tải ảnh dọn dẹp & AI kiểm chứng
                        with c2:
                            st.markdown("##### 🤖 XÁC MINH HOÀN THÀNH BẰNG AI")
                            if r_status == "Đã hoàn thành":
                                st.success("✅ Nhiệm vụ đã hoàn tất và được AI nghiệm thu!")
                                if r_clean_img and os.path.exists(r_clean_img):
                                    st.image(r_clean_img, caption="Ảnh hiện trường sau khi dọn dẹp", use_container_width=True)
                                st.markdown(f"**Kết quả thẩm định từ AI:**\n{r_ver_note}")
                            else:
                                st.write("Chụp/tải ảnh **mặt bằng đã sạch rác** lên để AI thẩm định:")
                                clean_file = st.file_uploader(f"Tải ảnh đã dọn xong (Nhiệm vụ #{r_id})", type=["jpg", "png", "jpeg"], key=f"up_staff_{r_id}")
                                
                                if clean_file and st.button(f"🚀 Gửi AI Thẩm Định #{r_id}", type="primary"):
                                    with st.spinner("🤖 AI đang so sánh ảnh trước & sau dọn dẹp..."):
                                        try:
                                            clean_img_obj = Image.open(clean_file)
                                            clean_save_path = UPLOAD_DIR / f"cleaned_{r_id}_{clean_file.name}"
                                            with open(clean_save_path, "wb") as f:
                                                f.write(clean_file.getbuffer())

                                            orig_img_obj = Image.open(r_img)
                                            
                                            verify_prompt = (
                                                "Bạn là AI thẩm định môi trường. Hãy so sánh 2 hình ảnh:\n"
                                                "Ảnh 1: Hiện trường ô nhiễm ban đầu.\n"
                                                "Ảnh 2: Báo cáo sau khi Đội tình nguyện / Cơ quan đã xử lý dọn dẹp.\n"
                                                "Đánh giá xem điểm này đã sạch sẽ rác thải chưa, chấm điểm từ 0-100 và đưa ra nhận xét ngắn."
                                            )

                                            v_resp = client.models.generate_content(
                                                model=model_name,
                                                contents=[orig_img_obj, clean_img_obj, verify_prompt],
                                                config={
                                                    "response_mime_type": "application/json",
                                                    "response_schema": VerificationSchema
                                                }
                                            )
                                            v_data = json.loads(v_resp.text)
                                            
                                            note = f"Thăng điểm sạch AI đánh giá: {v_data['confidence_score']}/100\nNhận xét: {v_data['ai_comment']}"
                                            update_resolution(r_id, str(clean_save_path), note)
                                            
                                            st.balloons()
                                            st.success("✅ Đã kiểm chứng và đánh dấu hoàn thành!")
                                            st.rerun()
                                        except Exception as e:
                                            st.error(f"Lỗi thẩm định AI: {e}")

        with tab_s2:
            st.markdown("#### 🗺️ Bản đồ quản lý các điểm phản ánh")
            if reports:
                m_staff = folium.Map(location=[reports[0][2], reports[0][3]], zoom_start=12, tiles="CartoDB positron")
                for r in reports:
                    color = "green" if r[10] == "Đã hoàn thành" else ("orange" if r[8] == "VOLUNTEER" else "red")
                    folium.Marker(
                        [r[2], r[3]],
                        popup=f"<b>{r[1]}</b><br>Phân loại: {r[8]}<br>Trạng thái: {r[10]}",
                        tooltip=f"{r[1]} ({r[10]})",
                        icon=folium.Icon(color=color, icon="leaf")
                    ).add_to(m_staff)
                st_folium(m_staff, height=450, width="100%", key="staff_map")
