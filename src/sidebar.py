import streamlit as st
from login import logout


def apply_sidebar_styles():
    st.markdown("""
        <style>
        [data-testid="stSidebar"] {
            background: linear-gradient(160deg, #1e3a5f 0%, #16213e 100%);
        }

        [data-testid="stSidebarCollapseButton"] button,
        [data-testid="stSidebarCollapsedControl"] button {
            color: white !important;
        }
        
        [data-testid="stSidebarCollapseButton"] button svg,
        [data-testid="stSidebarCollapsedControl"] button svg {
            fill: white !important;
            stroke: white !important;
        }

        [data-testid="stSidebarCollapsedControl"] button {
            background-color: #1e3a5f !important;
            border-radius: 50% !important;
        }

        .sidebar-title {
            color: #ffffff;
            font-size: 20px;
            font-weight: 700;
            padding: 10px 0 2px 0;
            letter-spacing: 0.5px;
        }

        .sidebar-subtitle {
            color: #7f9fbf;
            font-size: 11px;
            letter-spacing: 1.5px;
            text-transform: uppercase;
            margin-bottom: 12px;
        }

        /* User info badge */
        .sidebar-user {
            background: rgba(99,179,237,0.1);
            border: 1px solid rgba(99,179,237,0.2);
            border-radius: 10px;
            padding: 10px 14px;
            margin-bottom: 12px;
        }
        .sidebar-user-name {
            color: #ffffff;
            font-size: 14px;
            font-weight: 600;
            margin: 0;
        }
        .sidebar-user-role {
            color: #63b3ed;
            font-size: 11px;
            letter-spacing: 1px;
            text-transform: uppercase;
            margin: 2px 0 0 0;
        }

        /* Standard Navigation Buttons */
        [data-testid="stSidebar"] .stButton > button {
            width: 100%;
            background-color: rgba(255,255,255,0.05);
            color: #c8d8e8;
            border: 1px solid rgba(255,255,255,0.08);
            border-radius: 8px;
            padding: 11px 16px;
            font-size: 13px;
            font-weight: 500;
            text-align: left;
            margin-bottom: 6px;
            transition: all 0.2s ease;
        }

        [data-testid="stSidebar"] .stButton > button:hover {
            background-color: rgba(99, 179, 237, 0.15) !important;
            color: #63b3ed !important;
            border-color: #63b3ed !important;
        }

        /* Active Navigation Button Style */
        .active-btn [data-testid="stButton"] > button {
            background-color: rgba(99, 179, 237, 0.25) !important;
            color: #ffffff !important;
            border-color: #63b3ed !important;
            font-weight: 700 !important;
        }

        [data-testid="stSidebar"] hr {
            border-color: rgba(255,255,255,0.1);
            margin: 14px 0;
        }

        [data-testid="stSidebar"] .stCaption {
            color: #4a6a8a !important;
            font-size: 11px;
            text-align: center;
        }
        </style>
    """, unsafe_allow_html=True)


def render_sidebar() -> str:
    apply_sidebar_styles()

    # Read user details from session state
    role = st.session_state.get("user_role", "").strip().lower()
    user_name = st.session_state.get("user_name", "User")
    is_admin = role in ("admin", "super admin")

    # Set initial default page state based on role
    if "page" not in st.session_state:
        st.session_state["page"] = "upload" if is_admin else "dashboard"

    # Strict Role Guard: Redirect non-admins if they try to access 'upload'
    if not is_admin and st.session_state["page"] == "upload":
        st.session_state["page"] = "dashboard"

    current_page = st.session_state["page"]

    with st.sidebar:
        st.markdown('<p class="sidebar-title">⚙️ Logistics App</p>', unsafe_allow_html=True)
        st.markdown('<p class="sidebar-subtitle">Report Management</p>', unsafe_allow_html=True)

        # User Profile Badge
        role_label = role.upper() if role else "USER"
        st.markdown(
            f'<div class="sidebar-user">'
            f'<p class="sidebar-user-name">Hello, {user_name}</p>'
            f'<p class="sidebar-user-role">{role_label}</p>'
            f'</div>',
            unsafe_allow_html=True,
        )

        st.divider()

        # ── Navigation Tabs ──────────────────
        
        # 1. Upload Data Tab (Admin / Super Admin Only)
        if is_admin:
            if current_page == "upload":
                st.markdown('<div class="active-btn">', unsafe_allow_html=True)
                st.button("📤 Upload Data", key="btn_upload")
                st.markdown('</div>', unsafe_allow_html=True)
            else:
                if st.button("📤 Upload Data", key="btn_upload"):
                    st.session_state["page"] = "upload"
                    st.rerun()

        # 2. Dashboard Tab (All Roles)
        if current_page == "dashboard":
            st.markdown('<div class="active-btn">', unsafe_allow_html=True)
            st.button("📌 Dashboard", key="btn_dashboard")
            st.markdown('</div>', unsafe_allow_html=True)
        else:
            if st.button("📌 Dashboard", key="btn_dashboard"):
                st.session_state["page"] = "dashboard"
                st.rerun()

        st.divider()

        # Logout Button
        if st.button("🚪 Logout", key="btn_logout"):
            logout()

        st.caption("© 2026 Logistics App v1.0")

    return st.session_state["page"]