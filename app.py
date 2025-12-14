import streamlit as st
import os
import pickle
import base64
import datefinder
from datetime import datetime
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build
from bs4 import BeautifulSoup
import json

# 配置
SCOPES = [
    'https://www.googleapis.com/auth/gmail.readonly',
    'https://www.googleapis.com/auth/calendar.events'
]

DDL_KEYWORDS = ['作业', '报告', '项目', '论文', '截止', 'deadline', 'due', 'submission', 'submit', '交作业', '递交', '上交', 'ddl']

st.set_page_config(page_title="DDL Guardian", page_icon="📌")
st.title("📌 DDL Guardian - 你的邮箱DDL自动提取工具")
st.markdown("扫描你的Gmail，提取潜在DDL，只有你确认后才会添加到Google Calendar～")

# 从 Secrets 读取 credentials
if "credentials_json" not in st.secrets:
    st.error("未配置 credentials_json，请在 Streamlit Secrets 中添加！")
    st.stop()

creds_dict = json.loads(st.secrets["credentials_json"])

# OAuth 流程（云端友好版）
flow = Flow.from_client_config(
    {"installed": creds_dict["installed"]},
    scopes=SCOPES,
    redirect_uri=st.secrets.get("redirect_uri", "https://" + st.runtime.get_url() + "/") if st.runtime.exists() else "http://localhost:8501/"
)

session_state = st.session_state

if "auth_code_processed" not in session_state:
    session_state.auth_code_processed = False

if "creds" not in session_state:
    query_params = st.query_params
    if "code" in query_params and not session_state.auth_code_processed:
        auth_code = query_params["code"]
        flow.fetch_token(code=auth_code)
        session_state.creds = flow.credentials
        session_state.auth_code_processed = True
        st.query_params.clear()
        st.rerun()
    else:
        auth_url, _ = flow.authorization_url(prompt='consent')
        st.markdown(f"### 请先授权访问你的Gmail和Calendar")
        st.markdown(f"[{flow.client_config['installed']['client_id']} 已请求访问权限]")
        st.link_button("🔑 点击这里授权（会跳转Google登录）", auth_url, use_container_width=True)
        st.stop()

creds = session_state.creds

if creds.expired:
    if creds.refresh_token:
        creds.refresh(Request())
    else:
        st.error("授权已过期，请重新授权")
        st.stop()

# 构建服务
gmail_service = build('gmail', 'v1', credentials=creds)
calendar_service = build('calendar', 'v3', credentials=creds)

st.success("✅ 已成功连接你的Gmail和Google Calendar！")

# 其余函数保持不变（提取事件、添加日历）
def get_email_body(payload):
    body = ""
    if 'parts' in payload:
        for part in payload['parts']:
            if part['mimeType'] == 'text/plain' and 'data' in part['body']:
                body += base64.urlsafe_b64decode(part['body']['data']).decode('utf-8')
            elif part['mimeType'] == 'text/html' and 'data' in part['body']:
                html = base64.urlsafe_b64decode(part['body']['data']).decode('utf-8')
                body += BeautifulSoup(html, 'html.parser').get_text()
            elif part['mimeType'].startswith('multipart'):
                body += get_email_body(part)
    elif 'data' in payload['body']:
        body = base64.urlsafe_b64decode(payload['body']['data']).decode('utf-8')
    return body

def extract_potential_events(gmail_service):
    query = ' OR '.join(DDL_KEYWORDS) + ' newer_than:3m'
    results = gmail_service.users().messages().list(userId='me', q=query, maxResults=100).execute()
    messages = results.get('messages', [])
    events = []
    seen = set()
    for msg in messages:
        msg_data = gmail_service.users().messages().get(userId='me', id=msg['id'], format='full').execute()
        payload = msg_data['payload']
        headers = payload['headers']
        subject = next((h['value'] for h in headers if h['name'] == 'Subject'), '无主题')
        sender = next((h['value'] for h in headers if h['name'] == 'From'), '未知')
        body = get_email_body(payload)
        full_text = subject + "\n" + body
        dates = list(datefinder.find_dates(full_text, base_date=datetime.now()))
        valid_dates = [d for d in dates if d.date() >= datetime.now().date()]
        if valid_dates:
            deadline = min(valid_dates)
            key = (subject, deadline.date())
            if key not in seen:
                seen.add(key)
                events.append({
                    'subject': subject,
                    'sender': sender,
                    'deadline': deadline,
                    'snippet': msg_data.get('snippet', '')
                })
    return events

def add_to_calendar(calendar_service, event):
    event_body = {
        'summary': f"📌 DDL: {event['subject']}",
        'description': f"来自: {event['sender']}\n\n{event['snippet']}",
        'start': {'date': event['deadline'].strftime('%Y-%m-%d'), 'timeZone': 'Asia/Shanghai'},
        'end': {'date': event['deadline'].strftime('%Y-%m-%d'), 'timeZone': 'Asia/Shanghai'},
        'reminders': {'useDefault': False, 'overrides': [{'method': 'popup', 'minutes': 1440}, {'method': 'popup', 'minutes': 60}, {'method': 'popup', 'minutes': 10}]},
    }
    calendar_service.events().insert(calendarId='primary', body=event_body).execute()

if st.button("🔍 开始扫描潜在DDL", use_container_width=True):
    with st.spinner("正在扫描你的邮件..."):
        events = extract_potential_events(gmail_service)
    
    if not events:
        st.success("🎉 没有检测到新的潜在DDL，恭喜你暂时很清闲！")
    else:
        st.write(f"### 检测到 {len(events)} 个潜在DDL：")
        selected = []
        for i, ev in enumerate(events):
            with st.expander(f"{i+1}. **{ev['subject']}** - {ev['deadline'].strftime('%Y-%m-%d %A')}"):
                st.write(f"✉️ 发件人：{ev['sender']}")
                st.write(f"📜 预览：{ev['snippet'][:300]}...")
                if st.checkbox("确认添加到日历", key=f"check_{i}"):
                    selected.append(ev)
        
        if st.button("✅ 确认添加选中的DDL到Google Calendar", type="primary", use_container_width=True):
            with st.spinner("正在添加..."):
                for ev in selected:
                    add_to_calendar(calendar_service, ev)
            st.success("🎯 所有选中的DDL已成功添加到你的日历！")
            st.balloons()
