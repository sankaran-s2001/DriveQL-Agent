from __future__ import annotations

import datetime
from html import escape
import pandas as pd


TABLE_STYLE = """
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap');

* {
    box-sizing: border-box;
    margin: 0;
    padding: 0;
}

body {
    font-family: 'Inter', -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
    color: #334155;
    background-color: #f8fafc;
    line-height: 1.5;
    padding: 40px 24px;
}

.container {
    max-width: 1000px;
    margin: 0 auto;
}

/* Base Card Style */
.card {
    background: #ffffff;
    border: 1px solid #e2e8f0;
    border-radius: 12px;
    padding: 24px;
    margin-bottom: 24px;
    box-shadow: 0 1px 3px 0 rgba(0, 0, 0, 0.05);
}

.card h2 {
    font-size: 18px;
    font-weight: 600;
    color: #0f172a;
    margin-bottom: 8px;
}

/* Status Chips & Badges */
.badge-container {
    margin-bottom: 16px;
}

.badge-chip {
    font-size: 12px;
    font-weight: 500;
    padding: 4px 10px;
    border-radius: 6px;
    background: #f1f5f9;
    color: #334155;
    border: 1px solid #e2e8f0;
    display: inline-block;
    margin-right: 6px;
    margin-bottom: 6px;
}

.badge-chip.success {
    background: #ecfdf5;
    color: #065f46;
    border-color: #a7f3d0;
}

.badge-chip.warning {
    background: #fffbeb;
    color: #92400e;
    border-color: #fde68a;
}

.badge-chip.error {
    background: #fef2f2;
    color: #991b1b;
    border-color: #fca5a5;
}

/* Question Display */
.question-box {
    background: #eef2ff;
    border-left: 4px solid #4f46e5;
    padding: 16px 20px;
    margin-bottom: 20px;
    font-weight: 500;
    font-size: 15px;
    color: #1e1b4b;
    border-radius: 0 6px 6px 0;
}

/* Table Style UI (Explicit properties for email support) */
.table-responsive {
    overflow-x: auto;
    border: 1px solid #e2e8f0;
    border-radius: 12px;
    background: #ffffff;
    margin-top: 16px;
}

table.result-table, table.audit-table {
    border-collapse: collapse;
    width: 100%;
    font-size: 14px;
    text-align: left;
}

table.result-table th, table.audit-table th {
    background: #f8fafc;
    color: #0f172a;
    padding: 12px 16px;
    font-weight: 600;
    border-bottom: 1px solid #e2e8f0;
    border-right: 1px solid #e2e8f0;
    text-align: center !important;
}

table.result-table td, table.audit-table td {
    padding: 12px 16px;
    border-bottom: 1px solid #e2e8f0;
    border-right: 1px solid #e2e8f0;
    color: #334155;
}

table.result-table th:last-child, table.audit-table th:last-child,
table.result-table td:last-child, table.audit-table td:last-child {
    border-right: none;
}

table.result-table tr:last-child td, table.audit-table tr:last-child td {
    border-bottom: none;
}

table.result-table tr:nth-child(even) td, table.audit-table tr:nth-child(even) td {
    background-color: #fafbfd;
}

/* Error layout styling */
.error-box {
    background: #fef2f2;
    border: 1px solid #fca5a5;
    color: #991b1b;
    padding: 16px 20px;
    border-radius: 12px;
    font-size: 14px;
    font-weight: 500;
    margin-top: 12px;
}

@media print {
    body {
        background-color: #ffffff;
        padding: 0;
    }
    .card {
        box-shadow: none !important;
        border: 1px solid #cbd5e1 !important;
        page-break-inside: avoid;
    }
}
</style>
"""


def dataframe_to_html(dataframe: pd.DataFrame) -> str:
    if dataframe.empty:
        return "<p style='color: #64748b; font-size:14px; padding: 16px;'>No rows returned.</p>"

    return dataframe.to_html(
        index=False,
        border=0,
        classes="result-table",
        escape=True,
    )


def build_html_report(results: list[dict], sync_log: list[dict] | None = None, api_metrics: dict | None = None) -> str:
    sections: list[str] = []
    
    total_queries = len(results)
    successful_queries = sum(1 for r in results if not r.get("error"))
    total_retries = sum(r.get("retry_count", 0) for r in results)
    
    # Calculate response latencies
    total_latency = sum(r.get("total_latency", 0.0) for r in results)
    avg_latency = total_latency / total_queries if total_queries > 0 else 0.0

    # Build Data Quality Ingestion report if logs are present
    quality_table = ""
    if sync_log:
        quality_lines = []
        for item in sync_log:
            warning = item.get("warning")
            metrics = item.get("quality_metrics", {})
            dup_rows = metrics.get("duplicate_rows", 0)
            missing = metrics.get("missing_values", 0)
            mixed = metrics.get("mixed_types", [])
            enc_warn = metrics.get("encoding_warning")

            warnings_list = []
            if warning and "Skipped" not in warning:
                warnings_list.append(warning)
            if dup_rows > 0:
                warnings_list.append(f"{dup_rows} duplicate rows resolved")
            if missing > 0:
                warnings_list.append(f"{missing} null cells found")
            if mixed:
                warnings_list.append(f"Mixed types in: {', '.join(mixed)}")
            if enc_warn:
                warnings_list.append(enc_warn)

            if warnings_list:
                warning_desc = "; ".join(warnings_list)
                badge_class = "badge-chip warning"
            else:
                warning_desc = warning if warning else "Clean"
                if "Skipped" in warning_desc:
                    badge_class = "badge-chip"
                else:
                    badge_class = "badge-chip success"

            quality_lines.append(
                f"""
                <tr>
                    <td style="border-bottom: 1px solid #e2e8f0; padding: 12px 16px;">{escape(item["source_file"])}</td>
                    <td style="border-bottom: 1px solid #e2e8f0; padding: 12px 16px;"><code>{escape(item["table_name"])}</code></td>
                    <td style="border-bottom: 1px solid #e2e8f0; padding: 12px 16px;">{item.get("rows", 0)}</td>
                    <td style="border-bottom: 1px solid #e2e8f0; padding: 12px 16px;">{item.get("columns", 0)}</td>
                    <td style="border-bottom: 1px solid #e2e8f0; padding: 12px 16px;">
                        <span class="{badge_class}">
                            {escape(warning_desc)}
                        </span>
                    </td>
                </tr>
                """
            )
        
        quality_table = f"""
        <div class="card">
            <h2 style="font-size: 18px; font-weight: 600; color: #0f172a; margin-top: 0; margin-bottom: 8px; font-family: 'Inter', sans-serif;">Data Ingestion & Quality Audit Summary</h2>
            <p style="color: #64748b; font-size: 14px; margin-bottom: 16px; font-family: 'Inter', sans-serif;">
                Quality statistics and table schema mapping metrics evaluated during file synchronization.
            </p>
            <div class="table-responsive">
                <table class="audit-table" style="border-collapse: collapse; width: 100%; font-size: 14px; text-align: left;">
                    <thead>
                        <tr style="background-color: #f8fafc;">
                            <th style="padding: 12px 16px; font-weight: 600; border-bottom: 1px solid #e2e8f0;">Source File</th>
                            <th style="padding: 12px 16px; font-weight: 600; border-bottom: 1px solid #e2e8f0;">SQLite Table</th>
                            <th style="padding: 12px 16px; font-weight: 600; border-bottom: 1px solid #e2e8f0;">Rows</th>
                            <th style="padding: 12px 16px; font-weight: 600; border-bottom: 1px solid #e2e8f0;">Columns</th>
                            <th style="padding: 12px 16px; font-weight: 600; border-bottom: 1px solid #e2e8f0;">Audit Status</th>
                        </tr>
                    </thead>
                    <tbody>
                        {''.join(quality_lines)}
                    </tbody>
                </table>
            </div>
        </div>
        """

    for index, item in enumerate(results, start=1):
        question = escape(item["question"])
        sql = escape(item.get("sql", ""))
        
        retry_count = item.get("retry_count", 0)
        llm_lat = item.get("llm_latency", 0.0)
        db_lat = item.get("db_latency", 0.0)
        tot_lat = item.get("total_latency", 0.0)

        # Status badge
        if item.get("error"):
            status_badge = '<span class="badge-chip error">Failed</span>'
            content = f'''
            <div class="error-box">
                <strong>Analysis Error:</strong> {escape(item["error"])}
            </div>
            '''
        else:
            status_badge = '<span class="badge-chip success">Success</span>'
            content = f'<div class="table-responsive">{dataframe_to_html(item["dataframe"])}</div>'

        # Healing / Retry badge
        if retry_count > 0:
            retry_badge = f'<span class="badge-chip warning">Healed ({retry_count} retries)</span>'
        else:
            retry_badge = '<span class="badge-chip">0 Retries</span>'

        # SQL box (using standard styled email-safe HTML code layout)
        sql_wrapper = ""
        if sql:
            sql_wrapper = f"""
            <div style="background-color: #0f172a; border-radius: 8px; padding: 16px; margin-bottom: 20px; border: 1px solid #1e293b;">
                <div style="font-size: 11px; font-weight: 600; color: #94a3b8; text-transform: uppercase; margin-bottom: 8px; font-family: 'Inter', sans-serif;">Generated SQL Query</div>
                <pre style="color: #38bdf8; font-family: monospace; font-size: 13px; margin: 0; white-space: pre-wrap; word-break: break-all;">{sql}</pre>
            </div>
            """

        sections.append(
            f"""
            <div class="card">
                <h2 style="font-size: 18px; font-weight: 600; color: #0f172a; margin-top: 0; margin-bottom: 12px; font-family: 'Inter', sans-serif;">
                    {index}. Analysis Result
                </h2>
                <div class="badge-container">
                    {status_badge}
                    {retry_badge}
                    <span class="badge-chip">LLM: {llm_lat:.2f}s</span>
                    <span class="badge-chip">DB: {db_lat:.2f}s</span>
                    <span class="badge-chip" style="font-weight: 600; color: #4f46e5; border-color: #cbd5e1;">Total: {tot_lat:.2f}s</span>
                </div>
                <div class="question-box">
                    <strong>Question:</strong> {question}
                </div>
                {sql_wrapper}
                {content}
            </div>
            """
        )

    # Email-safe side-by-side KPI Row using standard HTML Table
    summary_html = f"""
    <table width="100%" cellpadding="0" cellspacing="10" border="0" style="margin-bottom: 24px; font-family: 'Inter', sans-serif;">
        <tr>
            <td width="25%" valign="top" style="background-color: #ffffff; border: 1px solid #e2e8f0; border-radius: 12px; padding: 16px; box-shadow: 0 1px 3px rgba(0,0,0,0.05);">
                <div style="font-size: 11px; font-weight: 600; color: #64748b; text-transform: uppercase; letter-spacing: 0.05em; margin-bottom: 8px;">Total Questions</div>
                <div style="font-size: 24px; font-weight: 700; color: #0f172a; line-height: 1;">{total_queries}</div>
            </td>
            <td width="25%" valign="top" style="background-color: #ffffff; border: 1px solid #e2e8f0; border-radius: 12px; padding: 16px; box-shadow: 0 1px 3px rgba(0,0,0,0.05);">
                <div style="font-size: 11px; font-weight: 600; color: #64748b; text-transform: uppercase; letter-spacing: 0.05em; margin-bottom: 8px;">Success Rate</div>
                <div style="font-size: 24px; font-weight: 700; color: #0f172a; line-height: 1;">{successful_queries}/{total_queries}</div>
            </td>
            <td width="25%" valign="top" style="background-color: #ffffff; border: 1px solid #e2e8f0; border-radius: 12px; padding: 16px; box-shadow: 0 1px 3px rgba(0,0,0,0.05);">
                <div style="font-size: 11px; font-weight: 600; color: #64748b; text-transform: uppercase; letter-spacing: 0.05em; margin-bottom: 8px;">Healing Retries</div>
                <div style="font-size: 24px; font-weight: 700; color: #0f172a; line-height: 1;">{total_retries}</div>
            </td>
            <td width="25%" valign="top" style="background-color: #ffffff; border: 1px solid #e2e8f0; border-radius: 12px; padding: 16px; box-shadow: 0 1px 3px rgba(0,0,0,0.05);">
                <div style="font-size: 11px; font-weight: 600; color: #64748b; text-transform: uppercase; letter-spacing: 0.05em; margin-bottom: 8px;">Avg Response</div>
                <div style="font-size: 24px; font-weight: 700; color: #0f172a; line-height: 1;">{avg_latency:.2f}s</div>
            </td>
        </tr>
    </table>
    """

    timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S local time")

    api_html = ""
    if api_metrics:
        api_html = f"""
        <div class="card" style="margin-bottom: 24px;">
            <h2 style="font-size: 16px; font-weight: 600; color: #0f172a; margin-top: 0; margin-bottom: 12px; font-family: 'Inter', sans-serif;">Gemini API Quota & Rate Limiting Metrics</h2>
            <table width="100%" style="font-size: 14px; color: #475569; border-collapse: collapse;">
                <tr>
                    <td style="padding: 8px 0; border-bottom: 1px solid #f1f5f9; font-weight: 500;">Total API Requests:</td>
                    <td style="padding: 8px 0; border-bottom: 1px solid #f1f5f9; text-align: right; font-family: monospace;">{api_metrics.get("total_requests", 0)}</td>
                </tr>
                <tr>
                    <td style="padding: 8px 0; border-bottom: 1px solid #f1f5f9; font-weight: 500;">Successful API Requests:</td>
                    <td style="padding: 8px 0; border-bottom: 1px solid #f1f5f9; text-align: right; font-family: monospace; color: #059669;">{api_metrics.get("successful_requests", 0)}</td>
                </tr>
                <tr>
                    <td style="padding: 8px 0; border-bottom: 1px solid #f1f5f9; font-weight: 500;">Rate Limited API Requests (HTTP 429):</td>
                    <td style="padding: 8px 0; border-bottom: 1px solid #f1f5f9; text-align: right; font-family: monospace; color: #dc2626;">{api_metrics.get("rate_limited_requests", 0)}</td>
                </tr>
                <tr>
                    <td style="padding: 8px 0; border-bottom: 1px solid #f1f5f9; font-weight: 500;">Total Pipeline Waiting Duration (429 Sleep):</td>
                    <td style="padding: 8px 0; border-bottom: 1px solid #f1f5f9; text-align: right; font-family: monospace;">{api_metrics.get("total_wait_time", 0.0):.2f} seconds</td>
                </tr>
                <tr>
                    <td style="padding: 8px 0; font-weight: 500;">Overall Questions Pipeline Duration:</td>
                    <td style="padding: 8px 0; text-align: right; font-family: monospace;">{api_metrics.get("overall_duration", 0.0):.2f} seconds</td>
                </tr>
            </table>
        </div>
        """

    # Email-safe side-by-side Header using standard HTML Table
    return f"""
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Automated Data Analysis Report</title>
        {TABLE_STYLE}
    </head>
    <body>
        <div class="container">
            <table width="100%" cellpadding="0" cellspacing="0" border="0" style="margin-bottom: 24px; font-family: 'Inter', sans-serif;">
                <tr>
                    <td valign="top">
                        <h1 style="font-size: 24px; font-weight: 700; color: #0f172a; margin: 0 0 6px 0;">Automated Data Analysis Report</h1>
                        <div style="font-size: 13px; color: #64748b;">Generated: {timestamp}</div>
                    </td>
                    <td valign="top" align="right" style="white-space: nowrap;">
                        <span style="font-size: 11px; font-weight: 600; padding: 4px 10px; border-radius: 9999px; background-color: #eef2ff; color: #4f46e5; border: 1px solid #e0e7ff; margin-right: 8px;">Environment: Production</span>
                        <span style="font-size: 11px; font-weight: 600; padding: 4px 10px; border-radius: 9999px; background-color: #ecfdf5; color: #065f46; border: 1px solid #a7f3d0;">System: Active</span>
                    </td>
                </tr>
            </table>
            
            <hr style="border: 0; border-top: 1px solid #e2e8f0; margin-bottom: 24px;" />
            
            {summary_html}
            {api_html}
            {quality_table}
            {''.join(sections)}
        </div>
    </body>
    </html>
    """
