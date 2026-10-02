import os
import json
import io
import sqlite3
from pathlib import Path
import streamlit as st
from PIL import Image
import google.generativeai as genai
import folium
from streamlit_folium import st_folium

# =========================================================
# 1. CẤU HÌNH TRANG & CSS GIAO DIỆN
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
# 2. CẤU HÌNH GOOGLE GEMINI AI & TỐI ƯU HÓA LƯỢT GỌI
# =========================================================
raw_gemini_key = st.secrets.get("GEMINI_API_KEY", "")
gemini_key = str(raw_gemini_key).strip().strip('"').strip("'")
gemini_model_name = st.secrets.get("GEMINI_MODEL", "gemini-1.5-flash").strip().strip('"').strip("'")

if gemini_key:
    genai.configure(api_key=gemini_key)

UPLOAD_DIR = Path("uploaded_images")
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

def compress_image(uploaded_file, max_size=800):
    """Nén ảnh nhỏ lại để giảm dung lượng mạng và tăng tốc độ xử lý của AI"""
    image = Image.open(uploaded_file)
    if image.mode in ("RGBA", "P"):
        image = image.convert("RGB")
    image.thumbnail((max_size, max_size))
    img_byte_arr = io.BytesIO()
    image.save(img_byte_arr, format='JPEG', quality=80)
    return img_byte_arr.getvalue()

# Sử dụng Cache để tiết kiệm 100% lượt gọi API nếu ảnh đã từng được gửi phân tích
@st.cache_data(show_spinner=False)
def call_gemini_vision_cached(image_bytes, system_prompt, json_example_str):
    if not gemini_key:
        raise Exception("Chưa cấu hình GEMINI_API_KEY trong Streamlit Secrets!")
        
    try:
        model = genai.GenerativeModel(gemini_model_name)
        image_part = {"mime_type": "image/jpeg", "data": image_bytes}
        
        full_prompt = f"""
        {system_prompt}
        
        BẠN BẮT BUỘC TRẢ VỀ DẠNG JSON THUẦN TÚY, KHÔNG DÙNG MARKDOWN, KHÔNG CÓ ```json. CẤU TRÚC JSON CHUẨN:
        {json_example_str}
        """

        response = model.generate_content([full_prompt, image_part])
        clean_text = response.text.replace("```json", "").replace("```", "").strip()
        return json.loads(clean_text), f"Gemini ({gemini_model_name})"
    except Exception as e:
        raise Exception(f"Lỗi kết nối Gemini AI ({gemini_model_name}): {str(e)}")

json_waste_example = {
    "contains_waste": True,
    "is_waste_amount_sufficient": True,
    "rejection_reason": "",
    "waste_type": "Rác nhựa, túi nilon",
    "severity": "Trung bình",
    "assigned_role": "VOLUNTEER",
    "action_plan": "Thu gom và phân loại rác thải"
}

json_verify_example = {
    "is_cleaned": True,
    "confidence_score": 85,
    "ai_comment": "Khu vực đã được dọn sạch rác.",
    "earned_points": 30
}

# =========================================================
# 3. CƠ SỞ DỮ LIỆU SQLITE
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
# 4. GIAO DIỆN CHÍNH
# =========================================================
if "user_role" not in st.session_state:
    st.session_state.user_role = None

if "is_processing" not in st.session_state:
    st.session_state.is_processing = False

STAFF_PIN = "1234"

st.markdown("""
    <div class="header-banner">
        <h1>🌱 URBAN GREENEYE AI – MẮT XANH ĐÔ THỊ</h1>
        <p>Hệ Thống Phản Ánh Môi Trường Tối Ưu Hóa Tích Hợp Google Gemini AI</p>
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
            <p style="color: #666; font-size: 0.92rem;">Gửi báo cáo rác thải, xem điểm đã dọn dẹp & bản đồ tương tác.</p>
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
            <p style="color: #666; font-size: 0.92rem;">Xử lý sự cố, thẩm định ảnh làm sạch qua AI và vinh danh.</p>
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
        role_badge = "🟢 Giao diện Người Dân Phản Ánh" if st.session_state.user_role == "CITIZEN" else "🛡 Dashboard Đội Tình Nguyện & Cơ Quan"
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
                    st.image(uploaded_file, caption="Ảnh thực tế đã tải", use_container_width=True)

            with col2:
                st.markdown("#### 🤖 3. Gemini Kiểm Duyệt & Anti-Spam")
                st.info("Hệ thống tự động kiểm duyệt hình ảnh thông minh và chống báo cáo ảo.")
                
                if st.button("🚀 GỬI BÁO CÁO NGAY", type="primary", use_container_width=True, disabled=st.session_state.is_processing):
                    if not uploaded_file:
                        st.error("⚠️ Vui lòng tải ảnh thực tế điểm ô nhiễm!")
                    else:
                        st.session_state.is_processing = True
                        with st.spinner("🤖 Gemini đang phân tích ảnh cực nhanh..."):
                            try:
                                # Nén ảnh tối ưu dung lượng trước khi lưu và gọi AI
                                compressed_bytes = compress_image(uploaded_file)
                                
                                save_path = UPLOAD_DIR / uploaded_file.name
                                with open(save_path, "wb") as f:
                                    f.write(compressed_bytes)

                                prompt = (
                                    "Phân tích bức ảnh này để báo cáo môi trường:\n"
                                    "1. KIỂM TRA RÁC: Bức ảnh có thực sự chứa rác thải gây ô nhiễm không? (Nếu là ảnh selfie, mặt người, cảnh vật sạch... -> contains_waste = False).\n"
                                    "2. LƯỢNG RÁC: Khối lượng rác có đủ lớn để tạo thành bãi ô nhiễm không? (Nếu quá nhỏ -> is_waste_amount_sufficient = False).\n"
                                    "3. Phân loại waste_type, severity (Thấp/Trung bình/Nghiêm trọng), và assigned_role: Rác nhẹ -> 'VOLUNTEER', Rác nặng/nguy hại -> 'AUTHORITY'."
                                )
                                
                                data, ai_engine = call_gemini_vision_cached(
                                    image_bytes=compressed_bytes,
                                    system_prompt=prompt,
                                    json_example_str=json.dumps(json_waste_example, ensure_ascii=False)
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
                                st.error(f"Lỗi hệ thống Gemini AI: {e}")
                            finally:
                                st.session_state.is_processing = False

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
                            st.info(f"🤖 **Gemini Thẩm Định:**\n{r_ver_note}")

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
                    role_label = "🧹 Đội Tình Nguyện" if r_role == "VOLUNTEER" else "🏛 Cơ Quan Chức Năng"
                    
                    with st.expander(f"[{status_badge}] Nhiệm vụ #{r_id}: {r_loc} — ({role_label})"):
                        c1, c2 = st.columns([1, 1], gap="medium")
                        with c1:
                            st.markdown("##### 📸 Bằng chứng ô nhiễm ban đầu:")
                            if os.path.exists(r_img): st.image(r_img, use_container_width=True)
                            st.markdown(f"• **📍 Vị trí:** {r_loc}")
                            st.markdown(f"• **🏷️ Loại rác:** {r_type} | **Mức độ:** {r_sev}")
                            st.info(f"💡 **Phương án gợi ý:** {r_plan}")

                        with c2:
                            st.markdown("##### 🤖 GEMINI AI THẨM ĐỊNH & TÍCH ĐIỂM")
                            if r_status == "Đã hoàn thành":
                                st.success(f"✅ Đã hoàn thành dọn dẹp bởi **{r_team}** (+{r_points} điểm vinh danh)")
                                if r_clean_img and os.path.exists(r_clean_img):
                                    st.image(r_clean_img, caption="Ảnh thực tế sau dọn dẹp", use_container_width=True)
                                st.markdown(f"**Nhận xét Gemini:**\n{r_ver_note}")
                            else:
                                team_name_input = st.text_input(f"🏷️ Tên Cá Nhân / Đội Dọn Dẹp (Mã #{r_id})", placeholder="VD: Đội Tình Nguyện Xanh 1", key=f"team_{r_id}")
                                clean_file = st.file_uploader(f"Tải ảnh đã dọn xong (Mã #{r_id})", type=["jpg", "png", "jpeg"], key=f"up_staff_{r_id}")
                                
                                if clean_file and st.button(f"🚀 Gemini Thẩm Định & Tích Điểm #{r_id}", type="primary"):
                                    if not team_name_input.strip():
                                        st.error("⚠️ Vui lòng nhập Tên Đội hoặc Cá Nhân dọn dẹp!")
                                    else:
                                        with st.spinner("🤖 Gemini đang đối chiếu ảnh trước và sau..."):
                                            try:
                                                compressed_clean_bytes = compress_image(clean_file)
                                                clean_save_path = UPLOAD_DIR / f"cleaned_{r_id}_{clean_file.name}"
                                                with open(clean_save_path, "wb") as f:
                                                    f.write(compressed_clean_bytes)

                                                verify_prompt = (
                                                    "So sánh bức ảnh dọn dẹp này với hiện trạng rác ban đầu:\n"
                                                    "1. Đánh giá tỷ lệ phần trăm dọn dẹp sạch sẽ (confidence_score từ 0 đến 100).\n"
                                                    "2. Only set is_cleaned = True khi tỷ lệ sạch đạt từ 70% trở lên.\n"
                                                    "3. Tích điểm earned_points (từ 10 đến 50 điểm) dựa trên lượng rác đã giải quyết nếu đạt >70%."
                                                )

                                                v_data, ai_engine = call_gemini_vision_cached(
                                                    image_bytes=compressed_clean_bytes,
                                                    system_prompt=verify_prompt,
                                                    json_example_str=json.dumps(json_verify_example, ensure_ascii=False)
                                                )
                                                
                                                score = v_data.get("confidence_score", 0)
                                                pts = v_data.get("earned_points", 0)
                                                is_clean = v_data.get("is_cleaned", False)
                                                
                                                if is_clean or score >= 70:
                                                    note = f"Thăng điểm dọn sạch qua {ai_engine}: {score}/100\nNhận xét: {v_data.get('ai_comment','')}"
                                                    update_resolution(r_id, str(clean_save_path), note, team_name_input.strip(), pts)
                                                    st.balloons()
                                                    st.success(f"🎉 Chúc mừng **{team_name_input}**! {ai_engine} xác minh đạt {score}% và cộng +{pts} điểm!")
                                                    st.rerun()
                                                else:
                                                    st.warning(f"⚠️ {ai_engine} đánh giá kết quả chỉ đạt **{score}%** (Chưa đạt mốc 70%). Vui lòng dọn dẹp thêm!")
                                            except Exception as e:
                                                st.error(f"Lỗi Gemini AI: {e}")

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
