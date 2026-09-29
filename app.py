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
# 1. CẤU HÌNH TRANG & CSS
# =========================================================
st.set_page_config(
    page_title="Smart Green Reporter - Quản Lý Môi Trường",
    page_icon="🌱",
    layout="wide"
)

st.markdown("""
    <style>
    .stApp { background-color: #f4f8f5; font-family: sans-serif; }
    [data-testid="stSidebar"] { display: none; }
    
    .header-banner {
        background: linear-gradient(135deg, #1b5e20 0%, #2e7d32 60%, #4caf50 100%);
        padding: 25px 20px;
        border-radius: 16px;
        color: white;
        text-align: center;
        margin-bottom: 25px;
        box-shadow: 0 6px 20px rgba(46, 125, 50, 0.18);
    }
    .header-banner h1 { color: white !important; font-weight: 800; margin-bottom: 6px; }
    
    .role-card {
        background: white; padding: 25px 20px; border-radius: 16px;
        border: 2px solid #c8e6c9; text-align: center;
    }
    
    .kpi-card {
        background: white; padding: 16px; border-radius: 12px;
        text-align: center; box-shadow: 0 2px 8px rgba(0,0,0,0.04);
        border-left: 5px solid #2e7d32;
    }
    .kpi-number { font-size: 1.8rem; font-weight: 800; color: #2e7d32; }
    .kpi-label { font-size: 0.88rem; color: #555; font-weight: 600; }

    /* Fix CSS Tab */
    .stTabs [data-baseweb="tab-list"] { gap: 12px !important; background-color: transparent !important; }
    .stTabs [data-baseweb="tab"] {
        height: auto !important; background-color: #ffffff !important;
        border-radius: 10px !important; padding: 12px 24px !important;
        font-weight: 700 !important; color: #2e7d32 !important;
        border: 1px solid #c8e6c9 !important;
    }
    .stTabs [aria-selected="true"] {
        background-color: #2e7d32 !important; color: #ffffff !important;
    }
    .stTabs [aria-selected="true"] p { color: #ffffff !important; }
    .stTabs [aria-selected="false"] p { color: #2e7d32 !important; }
    
    /* Bảng vinh danh */
    .leaderboard-card {
        background: #ffffff; padding: 15px; border-radius: 12px;
        border-left: 6px solid #fbc02d; box-shadow: 0 2px 8px rgba(0,0,0,0.05);
        margin-bottom: 10px;
    }
    </style>
""", unsafe_allow_html=True)

api_key = st.secrets.get("GEMINI_API_KEY")
model_name = st.secrets.get("GEMINI_MODEL", "gemini-2.5-flash")

if not api_key:
    st.error("⚠️ Chưa cấu hình GEMINI_API_KEY trong Secrets trên Streamlit Cloud!")
    st.stop()

client = genai.Client(api_key=api_key)
UPLOAD_DIR = Path("uploaded_images")
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

# Pydantic Schemas
class WasteAnalysisSchema(BaseModel):
    waste_type: str = Field(description="Các loại rác phát hiện trong hình")
    severity: str = Field(description="Mức độ ô nhiễm: Thấp, Trung bình, hoặc Nghiêm trọng")
    assigned_role: str = Field(description="Chỉ điền 'VOLUNTEER' hoặc 'AUTHORITY'")
    action_plan: str = Field(description="Phương án xử lý chi tiết")

class VerificationSchema(BaseModel):
    is_cleaned: bool = Field(description="True nếu địa điểm đã dọn dẹp sạch sẽ từ 70% trở lên, False nếu dưới 70%")
    confidence_score: int = Field(description="Thang điểm từ 0 đến 100 đánh giá tỷ lệ phần trăm đã được làm sạch")
    ai_comment: str = Field(description="Nhận xét chi tiết của AI về kết quả dọn dẹp")
    earned_points: int = Field(description="Điểm cộng vinh danh (từ 10 đến 50 điểm) nếu đạt tiêu chuẩn trên 70%, điền 0 nếu dưới 70%")

# =========================================================
# 2. CƠ SỞ DỮ LIỆU SQLITE
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
    
    # Auto Migration
    columns_to_check = [
        ("waste_type", "TEXT"), ("severity", "TEXT"), ("assigned_role", "TEXT"),
        ("action_plan", "TEXT"), ("status", "TEXT DEFAULT 'Chờ xử lý'"),
        ("cleaned_image_path", "TEXT"), ("verification_note", "TEXT"),
        ("cleaner_team", "TEXT"), ("points_earned", "INTEGER DEFAULT 0")
    ]
    for col_name, col_type in columns_to_check:
        try:
            c.execute(f"ALTER TABLE reports ADD COLUMN {col_name} {col_type}")
        except sqlite3.OperationalError:
            pass
            
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

# =========================================================
# 3. QUẢN LÝ SESSION
# =========================================================
if "user_role" not in st.session_state:
    st.session_state.user_role = None

STAFF_PIN = "1234"

st.markdown("""
    <div class="header-banner">
        <h1>🌱 SMART GREEN REPORTER</h1>
        <p>Hệ Thống Báo Cáo Môi Trường & Vinh Danh Tình Nguyện Viên AI</p>
    </div>
""", unsafe_allow_html=True)

# MÀN HÌNH CHỌN VAI TRÒ
if st.session_state.user_role is None:
    st.markdown("<h3 style='text-align: center; color: #1b5e20; font-weight:700;'>👋 CHỌN VAI TRÒ TRUY CẬP HỆ THỐNG</h3>", unsafe_allow_html=True)
    st.write("")
    
    _, col1, col2, _ = st.columns([0.3, 2, 2, 0.3], gap="large")
    
    with col1:
        st.markdown("""
        <div class="role-card">
            <div style="font-size:3rem; margin-bottom:10px;">👤</div>
            <h3 style="color: #2e7d32;">NGƯỜI DÂN PHẢN ÁNH</h3>
            <p style="color: #666; font-size: 0.92rem;">Gửi báo cáo rác thải kèm hình ảnh & ghim vị trí bản đồ.</p>
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
                st.error("❌ Mã PIN chưa đúng! (Mã thử nghiệm: 1234)")

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

    # GIAO DIỆN NGƯỜI DÂN
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
                description = st.text_area("Mô tả thêm (Tùy chọn)", placeholder="Mô tả hiện trạng rác thải...")
                uploaded_file = st.file_uploader("📸 2. Tải ảnh thực tế điểm ô nhiễm *", type=["jpg", "jpeg", "png"])
                
                if uploaded_file:
                    image = Image.open(uploaded_file)
                    st.image(image, caption="Ảnh thực tế đã tải", use_container_width=True)

            with col2:
                st.markdown("#### 🤖 3. AI Tự Động Phân Loại")
                st.info("Hệ thống AI sẽ tự động phân tích hình ảnh và chuyển tới đơn vị xử lý.")
                
                if st.button("🚀 GỬI BÁO CÁO NGAY", type="primary", use_container_width=True):
                    if not uploaded_file:
                        st.error("⚠️ Vui lòng tải ảnh thực tế!")
                    else:
                        with st.spinner("🤖 AI Gemini đang phân tích..."):
                            try:
                                save_path = UPLOAD_DIR / uploaded_file.name
                                with open(save_path, "wb") as f:
                                    f.write(uploaded_file.getbuffer())

                                prompt = "Phân tích ảnh rác thải này: 1. Rác nhẹ, đồ nhựa -> assigned_role='VOLUNTEER'. 2. Rác nguy hại, lớn -> assigned_role='AUTHORITY'."
                                response = client.models.generate_content(
                                    model=model_name, contents=[image, prompt],
                                    config={"response_mime_type": "application/json", "response_schema": WasteAnalysisSchema}
                                )
                                data = json.loads(response.text)
                                final_loc = location_name if location_name else f"Tọa độ ({selected_lat:.4f}, {selected_lng:.4f})"
                                save_report(final_loc, selected_lat, selected_lng, description, str(save_path), data["waste_type"], data["severity"], data["assigned_role"], data["action_plan"])
                                st.balloons()
                                st.success("🎉 Gửi phản ánh thành công!")
                            except Exception as e:
                                st.error(f"Lỗi: {e}")

        with tab_c2:
            st.markdown("#### 🗺️ Bản đồ các điểm ô nhiễm cộng đồng")
            reports = get_all_reports()
            if reports:
                m_all = folium.Map(location=[reports[0][2], reports[0][3]], zoom_start=12, tiles="CartoDB positron")
                for r in reports:
                    color = "green" if r[10] == "Đã hoàn thành" else "red"
                    folium.Marker([r[2], r[3]], popup=f"<b>{r[1]}</b><br>Trạng thái: {r[10]}", icon=folium.Icon(color=color, icon="leaf")).add_to(m_all)
                st_folium(m_all, height=450, width="100%", key="cit_map")

    # DASHBOARD ĐỘI TÌNH NGUYỆN & CƠ QUAN
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

        tab_s1, tab_s2, tab_s3 = st.tabs(["📋 Báo Cáo & AI Kiểm Chứng", "🏆 Bảng Vàng Vinh Danh", "🗺️ Bản Đồ Sự Cố"])
        
        # TAB 1: DANH SÁCH & AI CHECK
        with tab_s1:
            if not reports:
                st.info("🎉 Chưa có điểm ô nhiễm nào cần xử lý.")
            else:
                for r in reports:
                    r_id, r_loc, r_lat, r_lng, r_desc, r_img, r_type, r_sev, r_role, r_plan, r_status, r_clean_img, r_ver_note, r_team, r_points, r_time = r
                    
                    status_badge = "🟢 Đã hoàn thành" if r_status == "Đã hoàn thành" else "🔴 Chờ xử lý"
                    role_label = "🧹 Đội Tình Nguyện" if r_role == "VOLUNTEER" else "🏛️ Cơ Quan Chức Năng"
                    
                    with st.expander(f"[{status_badge}] Nhiệm vụ #{r_id}: {r_loc} — (Đơn vị: {role_label})"):
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
                                st.success(f"✅ Đã dọn dẹp xong bởi **{r_team}** (+{r_points} điểm vinh danh)")
                                if r_clean_img and os.path.exists(r_clean_img):
                                    st.image(r_clean_img, caption="Ảnh thực tế sau dọn dẹp", use_container_width=True)
                                st.markdown(f"**Đánh giá AI:**\n{r_ver_note}")
                            else:
                                team_name_input = st.text_input(f"🏷️ Tên Cá Nhân / Đội Dọn Dẹp (Mã #{r_id})", placeholder="VD: Đội Tình Nguyện Xanh 1", key=f"team_{r_id}")
                                clean_file = st.file_uploader(f"Tải ảnh đã dọn xong (Mã #{r_id})", type=["jpg", "png", "jpeg"], key=f"up_staff_{r_id}")
                                
                                if clean_file and st.button(f"🚀 AI Thẩm Định & Tích Điểm #{r_id}", type="primary"):
                                    if not team_name_input.strip():
                                        st.error("⚠️ Vui lòng nhập Tên Đội hoặc Cá Nhân dọn dẹp!")
                                    else:
                                        with st.spinner("🤖 AI đang thẩm định kết quả (Yêu cầu làm sạch > 70%)..."):
                                            try:
                                                clean_img_obj = Image.open(clean_file)
                                                clean_save_path = UPLOAD_DIR / f"cleaned_{r_id}_{clean_file.name}"
                                                with open(clean_save_path, "wb") as f:
                                                    f.write(clean_file.getbuffer())

                                                orig_img_obj = Image.open(r_img)
                                                
                                                verify_prompt = (
                                                    "So sánh 2 ảnh: Ảnh 1 (Ban đầu) và Ảnh 2 (Sau khi dọn dẹp).\n"
                                                    "1. Đánh giá tỷ lệ phần trăm dọn dẹp sạch rác thải (confidence_score từ 0 đến 100).\n"
                                                    "2. TIÊU CHUẨN XÁC NHẬN: Chỉ khi tỷ lệ sạch đạt từ 70% trở lên thì mới xét is_cleaned = True.\n"
                                                    "3. Nếu đạt trên 70%, tính số điểm thưởng earned_points (từ 10 đến 50 điểm) dựa trên khối lượng rác đã dọn dẹp. Nếu dưới 70%, earned_points = 0."
                                                )

                                                v_resp = client.models.generate_content(
                                                    model=model_name, contents=[orig_img_obj, clean_img_obj, verify_prompt],
                                                    config={"response_mime_type": "application/json", "response_schema": VerificationSchema}
                                                )
                                                v_data = json.loads(v_resp.text)
                                                
                                                score = v_data.get("confidence_score", 0)
                                                pts = v_data.get("earned_points", 0)
                                                is_clean = v_data.get("is_cleaned", False)
                                                
                                                if is_clean and score >= 70:
                                                    note = f"Thăng điểm sạch AI đánh giá: {score}/100 (Đạt tiêu chuẩn > 70%)\nNhận xét: {v_data['ai_comment']}"
                                                    update_resolution(r_id, str(clean_save_path), note, team_name_input.strip(), pts)
                                                    st.balloons()
                                                    st.success(f"🎉 Chúc mừng **{team_name_input}**! AI xác minh khu vực đã được làm sạch {score}% (> 70%) và nhận +{pts} điểm vinh danh!")
                                                    st.rerun()
                                                else:
                                                    st.warning(f"⚠️ AI đánh giá kết quả dọn dẹp mới đạt **{score}%** (Chưa đạt tiêu chuẩn tối thiểu 70%). Vui lòng tiếp tục dọn dẹp và gửi lại ảnh mới!")
                                                    st.info(f"**Nhận xét từ AI:** {v_data['ai_comment']}")
                                            except Exception as e:
                                                st.error(f"Lỗi AI: {e}")

        # TAB 2: BẢNG VÀNG VINH DANH
        with tab_s2:
            st.markdown("### 🏆 BẢNG XẾP HẠNG TÌNH NGUYỆN VIÊN / CƠ QUAN XUẤT SẮC")
            st.caption("Điểm số được AI tự động thẩm định và cộng thưởng khi kết quả dọn dẹp thực tế đạt trên 70%.")
            
            leaderboard_data = get_leaderboard()
            if not leaderboard_data:
                st.info("Chưa có đội nào hoàn thành nhiệm vụ đạt tiêu chuẩn > 70% để lên Bảng Vàng. Hãy dọn dẹp và tải ảnh lên ngay!")
            else:
                for idx, (team, total_pts, count) in enumerate(leaderboard_data, 1):
                    rank_icon = "🥇" if idx == 1 else ("🥈" if idx == 2 else ("🥉" if idx == 3 else f"#{idx}"))
                    st.markdown(f"""
                    <div class="leaderboard-card">
                        <div style="display: flex; justify-content: space-between; align-items: center;">
                            <div>
                                <h4 style="margin:0; color:#1b5e20;">{rank_icon} {team}</h4>
                                <span style="font-size:0.88rem; color:#666;">Đã hoàn thành: <b>{count} nhiệm vụ đạt chuẩn (> 70%)</b></span>
                            </div>
                            <div style="text-align:right;">
                                <span style="font-size:1.4rem; font-weight:800; color:#d81b60;">+{total_pts}</span>
                                <span style="font-size:0.85rem; color:#888;"> điểm</span>
                            </div>
                        </div>
                    </div>
                    """, unsafe_allow_html=True)

        # TAB 3: BẢN ĐỒ
        with tab_s3:
            st.markdown("#### 🗺️ Bản đồ quản lý sự cố")
            if reports:
                m_staff = folium.Map(location=[reports[0][2], reports[0][3]], zoom_start=12, tiles="CartoDB positron")
                for r in reports:
                    color = "green" if r[10] == "Đã hoàn thành" else ("orange" if r[8] == "VOLUNTEER" else "red")
                    folium.Marker([r[2], r[3]], popup=f"<b>{r[1]}</b><br>Trạng thái: {r[10]}", icon=folium.Icon(color=color, icon="leaf")).add_to(m_staff)
                st_folium(m_staff, height=450, width="100%", key="staff_map")
