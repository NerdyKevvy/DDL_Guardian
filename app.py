import streamlit as st
import os
import pickle
import base64
import datefinder
from datetime import datetime
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from bs4 import BeautifulSoup

# 需要额外安装：pip install streamlit beautifulsoup4 datefinder google-api-python-client google-auth-oauthlib

SCOPES = [
    'https://www.googleapis.com/auth/gmail.readonly',
    'https://www.googleapis.com/auth/calendar.events'
]

DDL_KEYWORDS = ['作业', '报告', '项目', '论文', '截止', 'deadline', 'due', 'submission', 'submit', '交作业', '递交', '上交', 'ddl']

st.title("📌 DDL Guardian - 你的邮箱DDL自动提取工具")
st.markdown("扫描你的Gmail，提取潜在DDL，只有你确认后才会添加到Google Calendar～")

# OAuth 流程（Streamlit 版）
def get_gmail_calendar_service():
    creds = None
    token_file = "token.pickle"  # Streamlit Cloud 会自动保存
    
    if os.path.exists(token_file):
        with open(token_file, "rb") as token:
            creds = pickle.load(token)
    
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
            with open(token_file, "wb") as token:
                pickle.dump(creds, token)
        else:
            flow = InstalledAppFlow.from_client_secrets_file("credentials.json", SCOPES)
            creds = flow.run_local_server(port=0)  # 会自动打开浏览器授权
            with open(token_file, "wb") as token:
                pickle.dump(creds, token)
    
    return build('gmail', 'v1', credentials=creds), build('calendar', 'v3', credentials=creds)

# 同之前的函数（略微简化）
def get_email_body(payload):
    body = ""
    if 'parts' in payload:
        for part in payload['parts']:
            if part['mimeType'] == 'text/plain':
                data = part['body'].get('data', '')
                if data:
                    body += base64.urlsafe_b64decode(data).decode('utf-8')
            elif part['mimeType'] == 'text/html':
                data = part['body'].get('data', '')
                if data:
                    html = base64.urlsafe_b64decode(data).decode('utf-8')
                    soup = BeautifulSoup(html, 'html.parser')
                    body += soup.get_text()
    else:
        data = payload['body'].get('data', '')
        if data:
            body = base64.urlsafe_b64decode(data).decode('utf-8')
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
            key = (subject, deadline)
            if key not in seen:
                seen.add(key)
                events.append({
                    'subject': subject,
                    'sender': sender,
                    'deadline': deadline,
                    'snippet': msg_data['snippet']
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

# 主逻辑
try:
    gmail_service, calendar_service = get_gmail_calendar_service()
    st.success("✅ 已连接你的Gmail和Calendar！")
    
    if st.button("🔍 开始扫描潜在DDL"):
        with st.spinner("正在扫描邮件..."):
            events = extract_potential_events(gmail_service)
        
        if not events:
            st.info("🎉 没有检测到新的DDL，恭喜暂时清闲！")
        else:
            st.write(f"检测到 **{len(events)}** 个潜在DDL：")
            selected = []
            for i, ev in enumerate(events):
                with st.expander(f"{i+1}. {ev['subject']} - {ev['deadline'].strftime('%Y-%m-%d %A')}"):
                    st.write(f"✉️ 发件人：{ev['sender']}")
                    st.write(f"📜 预览：{ev['snippet'][:200]}...")
                    if st.checkbox("确认添加到日历", key=f"check_{i}"):
                        selected.append(ev)
            
            if st.button("✅ 确认添加选中的事件到Calendar"):
                for ev in selected:
                    add_to_calendar(calendar_service, ev)
                st.success("已成功添加选中的DDL到你的日历！📅")
                
except Exception as e:
    st.error("请先授权访问你的Gmail和Calendar（会自动弹出浏览器窗口）")
    st.error(str(e))