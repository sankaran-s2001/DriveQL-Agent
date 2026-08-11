from pathlib import Path
import re
import datetime

import streamlit as st
import streamlit.components.v1 as components

from config import settings, StartupValidationError
from pipeline import (
    sync_google_drive_to_sqlite,
    answer_questions,
    create_report,
    email_report,
    validate_emails,
)

# 1. Page Configuration
st.set_page_config(
    page_title="Enterprise Data Analytics Agent",
    page_icon="🤖",
    layout="wide",
)

# 2. Centralized Adaptive Design System (Dark/Light Auto-Theming)
st.html("""
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap');

/* Global Font Override & Background Integration */
.stApp {
    font-family: 'Inter', sans-serif;
    background-color: var(--background-color) !important;
    color: var(--text-color) !important;
}

/* Sidebar Custom Styling - Auto Adaptive */
section[data-testid="stSidebar"] {
    background-color: var(--secondary-background-color) !important;
    color: var(--text-color) !important;
    border-right: 1px solid rgba(128, 128, 128, 0.15);
    padding: 24px 16px;
}
section[data-testid="stSidebar"] h1, 
section[data-testid="stSidebar"] h2, 
section[data-testid="stSidebar"] h3, 
section[data-testid="stSidebar"] h4, 
section[data-testid="stSidebar"] h5, 
section[data-testid="stSidebar"] span,
section[data-testid="stSidebar"] label {
    color: var(--text-color) !important;
}

/* Protect white text on branding logo box */
.brand-icon-text {
    color: #ffffff !important;
}

/* Input boxes & Text area custom style */
.stTextArea textarea {
    border-radius: 12px !important;
    border: 1px solid rgba(128, 128, 128, 0.2) !important;
    font-family: 'Inter', sans-serif !important;
    font-size: 14px !important;
    padding: 16px !important;
}
.stTextArea textarea:focus {
    border-color: var(--primary-color, #4f46e5) !important;
    box-shadow: 0 0 0 3px rgba(79, 70, 229, 0.15) !important;
}

/* Premium Adaptive Cards */
.kpi-card {
    background-color: var(--secondary-background-color);
    color: var(--text-color);
    border: 1px solid rgba(128, 128, 128, 0.15);
    border-radius: 12px;
    padding: 20px;
    box-shadow: 0 1px 3px rgba(0, 0, 0, 0.05);
    transition: all 0.2s ease-in-out;
}
.kpi-card:hover {
    transform: translateY(-2px);
    box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.05);
    border-color: var(--primary-color, #4f46e5);
}

.timeline-step {
    background: rgba(79, 70, 229, 0.08);
    border: 1px solid rgba(79, 70, 229, 0.2);
    padding: 8px 12px;
    border-radius: 8px;
    font-size: 13px;
    font-weight: 500;
    color: var(--primary-color, #4f46e5);
    display: inline-flex;
    align-items: center;
    gap: 6px;
}

/* Embedded HTML Iframe window integration */
iframe {
    border: 1px solid rgba(128, 128, 128, 0.2) !important;
    border-radius: 12px !important;
    background-color: var(--secondary-background-color) !important;
}
</style>
""")

# 3. Startup Validation Shield
try:
    settings.validate_startup()
except StartupValidationError as err:
    st.error("### ⚠️ Configuration & Startup Check Failed")
    st.markdown("The application could not be launched due to the following configuration issues:")
    for error_msg in str(err).splitlines():
        st.markdown(f"* {error_msg}")
    st.warning("Please correct your `.env` variables or service credentials and restart the dashboard.")
    st.stop()

# 4. Sidebar Navigation Panel
with st.sidebar:
    st.markdown(
        """
        <div style="display: flex; align-items: center; gap: 10px; margin-bottom: 20px;">
            <div style="background: var(--primary-color, #4f46e5); color: #ffffff; width: 36px; height: 36px; border-radius: 8px; display: flex; align-items: center; justify-content: center; font-weight: 700; font-size: 18px;"><span class="brand-icon-text">A</span></div>
            <div>
                <div style="font-weight: 700; font-size: 16px; color: var(--text-color); line-height: 1.2;">Analytics Agent</div>
                <div style="font-size: 11px; color: var(--text-color); opacity: 0.7; font-weight: 500; text-transform: uppercase; letter-spacing: 0.05em;">Enterprise Edition</div>
            </div>
        </div>
        """,
        unsafe_allow_html=True
    )
    
    st.divider()
    
    st.subheader("Ingestion Management")
    if st.button("Synchronize Drive Folder", width="stretch", type="secondary"):
        with st.spinner("Downloading and parsing SQLite tables..."):
            try:
                load_log = sync_google_drive_to_sqlite()
                st.session_state["sync_log"] = load_log
                st.success("Synchronized successfully.")
            except Exception as exc:
                st.error(f"Sync failed: {str(exc)}")

    if "sync_log" in st.session_state:
        st.markdown("<div style='font-size: 12px; font-weight: 600; margin-top: 12px; margin-bottom: 8px;'>Synced Catalog</div>", unsafe_allow_html=True)
        for item in st.session_state["sync_log"]:
            badge = "🔄 Skipped" if "Skipped" in str(item.get("warning", "")) else "✅ Loaded"
            st.caption(f"**{badge} {item['table_name']}** ({item['rows']} rows)")

    st.divider()

    st.subheader("Recipient Config")
    st.markdown("<div style='font-size: 11px; color: var(--text-color); opacity: 0.8; margin-bottom: 8px;'>Enter one or more email addresses separated by commas or semicolons.</div>", unsafe_allow_html=True)

    if "recipient_emails" not in st.session_state:
        st.session_state["recipient_emails"] = settings.email_to

    recipient_input = st.text_input(
        "Recipient Emails",
        value=st.session_state["recipient_emails"],
        placeholder="john@gmail.com, manager@company.com",
        label_visibility="collapsed"
    )
    st.session_state["recipient_emails"] = recipient_input

    valid_recipients, invalid_recipients = validate_emails(recipient_input)
    st.session_state["valid_recipients"] = valid_recipients
    st.session_state["invalid_recipients"] = invalid_recipients

    if not recipient_input.strip():
        fallback_list, _ = validate_emails(settings.email_to)
        st.session_state["valid_recipients"] = fallback_list
        st.warning("⚠️ Using default recipient from config.")
    elif invalid_recipients:
        st.error(f"❌ Invalid format: {', '.join(invalid_recipients)}")
    else:
        st.success(f"✅ {len(valid_recipients)} recipient(s) validated.")

    st.divider()
    st.markdown(
        f"""
        <div style="font-size: 11px; color: var(--text-color); opacity: 0.75; line-height: 1.6;">
            <b>Database Path:</b> {settings.sqlite_db_path.name}<br>
            <b>Gemini Model:</b> {settings.gemini_model}<br>
            <b>Environment:</b> Production
        </div>
        """,
        unsafe_allow_html=True
    )

# 5. Top Header & Subtitle
st.markdown(
    f"""
    <div style="display: flex; justify-content: space-between; align-items: flex-start; flex-wrap: wrap; gap: 20px; margin-bottom: 24px;">
        <div>
            <h1 style="color: var(--text-color); font-weight: 700; letter-spacing: -0.025em; margin-bottom: 6px;">Enterprise AI Data Analytics Workspace</h1>
            <div style="color: var(--text-color); opacity: 0.8; font-size: 14px; font-weight: 500;">Download folders, translate English questions to safe SQLite queries, and deliver HTML summary analytics.</div>
        </div>
        <div style="display: flex; gap: 10px; align-items: center;">
            <span style="font-size: 11px; font-weight: 600; text-transform: uppercase; padding: 4px 10px; border-radius: 9999px; background: rgba(79, 70, 229, 0.1); color: var(--primary-color, #4f46e5); border: 1px solid rgba(79, 70, 229, 0.2);">Env: Production</span>
            <span style="font-size: 11px; font-weight: 600; text-transform: uppercase; padding: 4px 10px; border-radius: 9999px; background: rgba(16, 185, 129, 0.1); color: #10b981; border: 1px solid rgba(16, 185, 129, 0.2);">System: Active</span>
        </div>
    </div>
    """,
    unsafe_allow_html=True
)

# 6. System Health Indicator Cards
col1, col2, col3 = st.columns(3)
with col1:
    st.markdown(
        """
        <div class="kpi-card">
            <div style="font-size: 11px; font-weight: 600; color: var(--text-color); opacity: 0.7; text-transform: uppercase; letter-spacing: 0.05em; margin-bottom: 8px;">Google Drive Storage</div>
            <div style="font-size: 18px; font-weight: 700; color: #10b981;">🟢 Authenticated</div>
            <div style="font-size: 12px; color: var(--text-color); opacity: 0.6; margin-top: 4px;">Service account reader active</div>
        </div>
        """,
        unsafe_allow_html=True
    )
with col2:
    st.markdown(
        """
        <div class="kpi-card">
            <div style="font-size: 11px; font-weight: 600; color: var(--text-color); opacity: 0.7; text-transform: uppercase; letter-spacing: 0.05em; margin-bottom: 8px;">SQLite Database</div>
            <div style="font-size: 18px; font-weight: 700; color: #10b981;">🟢 Connected & Writable</div>
            <div style="font-size: 12px; color: var(--text-color); opacity: 0.6; margin-top: 4px;">Schema reflected successfully</div>
        </div>
        """,
        unsafe_allow_html=True
    )
with col3:
    st.markdown(
        f"""
        <div class="kpi-card">
            <div style="font-size: 11px; font-weight: 600; color: var(--text-color); opacity: 0.7; text-transform: uppercase; letter-spacing: 0.05em; margin-bottom: 8px;">AI Model Integration</div>
            <div style="font-size: 18px; font-weight: 700; color: #10b981;">🟢 {settings.gemini_model}</div>
            <div style="font-size: 12px; color: var(--text-color); opacity: 0.6; margin-top: 4px;">Google AI Studio ready</div>
        </div>
        """,
        unsafe_allow_html=True
    )

st.divider()

# 7. Analytics Workspace
st.markdown("### Analytics Workspace")
try:
    with open("questions.txt", "r", encoding="utf-8") as f:
        default_questions = f.read().strip()
except Exception:
    default_questions = """What is the total number of records in each table?
Show total sales by region.
Show the average order value by customer segment.
Show the top 10 products by sales.
"""

question_text = st.text_area(
    "Enter business questions (one per line):",
    value=default_questions,
    height=160,
)

# Render input counts
lines = [l.strip() for l in question_text.splitlines() if l.strip()]
st.caption(f"📝 {len(lines)} questions parsed | {len(question_text)} characters")

run_clicked = st.button(
    "Run Analytics & Generate Report",
    type="primary",
    width="stretch"
)

if run_clicked:
    questions = [
        line.strip()
        for line in question_text.splitlines()
        if line.strip()
    ]

    if not questions:
        st.warning("Please enter at least one question to analyze.")
    else:
        # Visual Progress Steps Dashboard
        progress_area = st.container()
        with progress_area:
            st.markdown(
                """
                <div style="display: flex; gap: 8px; justify-content: space-between; font-size: 12px; margin-bottom: 16px; color: #64748b; flex-wrap: wrap;">
                    <span class="timeline-step">🟢 1. Startup Validation</span>
                    <span class="timeline-step">🟢 2. Schema cache check</span>
                    <span class="timeline-step">🔵 3. Translating Gemini SQL</span>
                    <span class="timeline-step">⚪ 4. Running queries</span>
                    <span class="timeline-step">⚪ 5. Compiling HTML Report</span>
                </div>
                """,
                unsafe_allow_html=True
            )
        
        from nl_to_sql_agent import GeminiRateLimiter
        import time

        progress_bar = st.progress(0.1, text="Initializing Gemini Quota Manager...")
        status_placeholder = st.empty()

        def update_ui(limiter):
            try:
                progress_text = f"Processing questions: {limiter.success_requests}/{len(questions)} completed. Status: {limiter.current_status}"
                progress_val = min(1.0, max(0.1, (limiter.success_requests / len(questions)) * 0.6 + 0.1))
                progress_bar.progress(progress_val, text=progress_text)
                
                status_text = f"""
                <div class="kpi-card" style="margin-bottom:16px;">
                    <div style="font-weight: 600; font-size:14px; margin-bottom:8px; color: var(--text-color);">🤖 Gemini Quota & Concurrency Orchestrator</div>
                    <div style="font-size:13px; margin-bottom:4px;"><b>API Status:</b> {limiter.current_status}</div>
                    <div style="font-size:13px; margin-bottom:4px;"><b>Requests:</b> {limiter.success_requests} Success / {limiter.total_requests} Total</div>
                    <div style="font-size:13px; margin-bottom:4px;"><b>429 Quota Exceeded Events:</b> <span style="color: #ef4444; font-weight:600;">{limiter.limited_requests}</span></div>
                    <div style="font-size:13px;"><b>Accumulated Cooldown Wait Time:</b> {limiter.total_wait_time:.1f}s</div>
                </div>
                """
                status_placeholder.markdown(status_text, unsafe_allow_html=True)
            except Exception:
                pass

        limiter = GeminiRateLimiter(
            max_requests_per_min=settings.gemini_max_requests_per_minute,
            max_concurrent=settings.gemini_max_concurrent_requests,
            retry_buffer=settings.gemini_retry_after_buffer,
            default_retry=settings.gemini_default_retry_seconds,
            max_wait=settings.gemini_max_wait_seconds,
            status_callback=update_ui,
        )

        t_start = time.time()
        results = answer_questions(questions, rate_limiter=limiter)
        overall_duration = time.time() - t_start
        
        progress_bar.progress(0.7, text="Writing report document...")
        sync_log = st.session_state.get("sync_log")
        
        api_metrics = {
            "total_requests": limiter.total_requests,
            "successful_requests": limiter.success_requests,
            "rate_limited_requests": limiter.limited_requests,
            "total_wait_time": limiter.total_wait_time,
            "overall_duration": overall_duration,
        }
        
        html = create_report(
            results,
            Path("output/analysis_report.html"),
            sync_log=sync_log,
            api_metrics=api_metrics,
        )
        
        progress_bar.progress(1.0, text="Process complete.")
        st.success("Execution completed successfully.")
        
        st.session_state["analysis_results"] = results
        st.session_state["analysis_html"] = html

# 8. Render Analysis Results Tray & report center
if "analysis_results" in st.session_state:
    tab1, tab2 = st.tabs(["📊 Question Results Tray", "👁️ HTML Report Preview"])
    
    with tab1:
        st.markdown("### Execution Analytics Tray")
        for index, item in enumerate(
            st.session_state["analysis_results"],
            start=1,
        ):
            is_error = bool(item.get("error"))
            badge_color = "background: rgba(239, 68, 68, 0.1); color: #ef4444; border: 1px solid rgba(239, 68, 68, 0.2);" if is_error else "background: rgba(16, 185, 129, 0.1); color: #10b981; border: 1px solid rgba(16, 185, 129, 0.2);"
            badge_text = "🔴 Failed" if is_error else "🟢 Success"
            
            with st.expander(
                f"{index}. {item['question']}",
                expanded=True,
            ):
                st.markdown(
                    f"""
                    <div style="display: flex; gap: 8px; margin-bottom: 12px; flex-wrap: wrap;">
                        <span style="font-size: 11px; font-weight: 600; padding: 2px 8px; border-radius: 4px; {badge_color}">{badge_text}</span>
                        <span style="font-size: 11px; font-weight: 500; background: var(--secondary-background-color, #f1f5f9); color: var(--text-color, #475569); padding: 2px 8px; border-radius: 4px; border: 1px solid rgba(128, 128, 128, 0.2);">Heal Attempts: {item['retry_count']}</span>
                        <span style="font-size: 11px; font-weight: 500; background: var(--secondary-background-color, #f1f5f9); color: var(--text-color, #475569); padding: 2px 8px; border-radius: 4px; border: 1px solid rgba(128, 128, 128, 0.2);">LLM: {item['llm_latency']:.2f}s</span>
                        <span style="font-size: 11px; font-weight: 500; background: var(--secondary-background-color, #f1f5f9); color: var(--text-color, #475569); padding: 2px 8px; border-radius: 4px; border: 1px solid rgba(128, 128, 128, 0.2);">DB: {item['db_latency']:.2f}s</span>
                        <span style="font-size: 11px; font-weight: 600; background: rgba(79, 70, 229, 0.1); color: var(--primary-color, #4f46e5); padding: 2px 8px; border-radius: 4px; border: 1px solid rgba(79, 70, 229, 0.2); font-weight: 600;">Total: {item['total_latency']:.2f}s</span>
                    </div>
                    """,
                    unsafe_allow_html=True
                )
                
                if item.get("error"):
                    st.error(item["error"])
                else:
                    st.code(item["sql"], language="sql")
                    st.dataframe(
                        item["dataframe"],
                        width="stretch"
                    )
                    
    with tab2:
        st.markdown("### HTML Report Document Center")
        
        # Dashboard Action Cards
        col_dl, col_mail = st.columns(2)
        with col_dl:
            st.download_button(
                "Download Compiled HTML Report",
                data=st.session_state["analysis_html"],
                file_name="analysis_report.html",
                mime="text/html",
                type="primary",
                width="stretch"
            )
        with col_mail:
            invalid_list = st.session_state.get("invalid_recipients", [])
            valid_list = st.session_state.get("valid_recipients", [])
            button_disabled = len(invalid_list) > 0 or len(valid_list) == 0

            if button_disabled:
                st.button(
                    "Send HTML Report via Email",
                    width="stretch",
                    type="secondary",
                    disabled=True,
                    help="Please resolve email configuration errors in the sidebar first."
                )
            else:
                if st.button("Send HTML Report via Email", width="stretch", type="secondary"):
                    with st.spinner("Dispatching SMTP report email..."):
                        try:
                            recipients_str = ", ".join(valid_list)
                            email_report(st.session_state["analysis_html"], recipients_str)
                            now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S local time")
                            st.success(f"Email successfully delivered at {now_str} to: {recipients_str}")
                        except Exception as exc:
                            st.error(f"Delivery failed: {str(exc)}")

        st.divider()
        # Embed local HTML report file inside st.iframe (removes components.html warnings)
        st.iframe(src="output/analysis_report.html", height=600, width="stretch")
