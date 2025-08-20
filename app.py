# app.py（中文界面）
import json
import base64
import csv
import io
import re
from datetime import datetime
from typing import List, Dict, Tuple, Any

import streamlit as st

# =========================
# 核心逻辑（保留你的解析流程）
# =========================

def decode_content(content):
    """尝试用多种方式解码响应文本为 JSON。"""
    if not content:
        return None

    if isinstance(content, str):
        try:
            return json.loads(content)
        except json.JSONDecodeError:
            content = content.strip()
            if content.startswith('{') and content.endswith('}'):
                try:
                    return json.loads(content)
                except json.JSONDecodeError:
                    pass

    encodings = ['utf-8', 'latin1', 'cp1252', 'ascii']

    if isinstance(content, str):
        try:
            decoded = base64.b64decode(content, validate=False)
            for encoding in encodings:
                try:
                    text = decoded.decode(encoding, errors='strict')
                    try:
                        return json.loads(text)
                    except json.JSONDecodeError:
                        pass
                except UnicodeDecodeError:
                    continue
        except Exception:
            pass

    if isinstance(content, (bytes, bytearray)):
        for encoding in encodings:
            try:
                text = content.decode(encoding)
                try:
                    return json.loads(text)
                except json.JSONDecodeError:
                    pass
            except UnicodeDecodeError:
                continue

    return None


def extract_orders(data: Dict[str, Any]) -> Tuple[List[Dict[str, Any]], Dict[str, int]]:
    """从接口响应中提取订单信息。"""
    orders = []
    phone_sources = {'recharge_data': 0, 'buyer_phone': 0, 'nickname': 0}

    try:
        if not isinstance(data, dict):
            return orders, phone_sources
        if data.get('code') != 0:
            return orders, phone_sources

        order_list = data.get('orderList', [])
        for order in order_list:
            if not isinstance(order, dict):
                continue

            buyer_info = order.get('buyerInfo', {})
            common_info = order.get('commonInfo', {})

            # 按优先级提取手机号
            phone = ''
            accept_info = order.get('acceptInfo', {})
            recharge_data = accept_info.get('rechargeData', {})
            content = recharge_data.get('content', '')
            if content:
                match = re.search(r'(1[3-9]\d{9})', content)
                if match:
                    phone = match.group(1)
                    phone_sources['recharge_data'] += 1

            if not phone:
                phone = buyer_info.get('phone', '')
                if phone:
                    phone_sources['buyer_phone'] += 1

            if not phone:
                nick = buyer_info.get('nickName', '')
                match = re.search(r'(1[3-9]\d{9})', nick)
                if match:
                    phone = match.group(1)
                    phone_sources['nickname'] += 1

            order_data = {
                'Order ID': common_info.get('orderId', ''),
                'Buyer Nickname': buyer_info.get('nickName', ''),
                'Status': common_info.get('statusStr', ''),
                '手机号': phone
            }
            orders.append(order_data)

    except Exception:
        pass

    return orders, phone_sources


def extract_data(har_data: Dict[str, Any]) -> Tuple[List[Dict[str, Any]], Dict[str, int]]:
    """从 HAR 文件的 entries 中提取订单数据。"""
    extracted_data: List[Dict[str, Any]] = []
    total_phone_sources = {'recharge_data': 0, 'buyer_phone': 0, 'nickname': 0}

    if not har_data or 'log' not in har_data or 'entries' not in har_data['log']:
        return extracted_data, total_phone_sources

    entries = har_data['log']['entries']
    for entry in entries:
        try:
            request = entry.get('request', {})
            url = request.get('url', '')
            if 'orderSearch' not in url:
                continue

            response = entry.get('response', {})
            content = response.get('content', {})
            if not content or not content.get('text'):
                continue

            mime_type = content.get('mimeType', 'unknown')
            if not isinstance(mime_type, str) or not mime_type.lower().startswith('application/json'):
                continue

            data = decode_content(content.get('text', ''))
            if not data:
                continue

            orders, phone_sources = extract_orders(data)
            if orders:
                extracted_data.extend(orders)
                for k, v in phone_sources.items():
                    total_phone_sources[k] += v

        except Exception:
            continue

    return extracted_data, total_phone_sources


def har_bytes_to_json(b: bytes):
    """将 HAR（二进制）解析为 JSON。"""
    for enc in ('utf-8', 'utf-16', 'latin1'):
        try:
            return json.loads(b.decode(enc))
        except Exception:
            continue
    try:
        return json.loads(b)
    except Exception:
        return None


def to_csv_bytes(rows: List[Dict[str, Any]]) -> bytes:
    """将结果行写成 CSV（二进制，UTF-8-SIG）。"""
    if not rows:
        return b''

    safe_rows = []
    for r in rows:
        r = dict(r)
        # 为避免 Excel 将长订单号转科学计数法，前置零宽空格
        r['Order ID'] = '\u200B' + str(r.get('Order ID', ''))
        safe_rows.append(r)

    field_order = ['Order ID', 'Buyer Nickname', 'Status', '手机号']
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=field_order, quoting=csv.QUOTE_ALL)
    writer.writeheader()
    writer.writerows(safe_rows)
    return buf.getvalue().encode('utf-8-sig')


# =========================
# Streamlit 界面（中文）
# =========================

st.set_page_config(page_title="HAR 转 CSV", page_icon="📦", layout="centered")
st.title("📦 HAR 转 CSV")
st.markdown(
    "上传一个或多个 `.har` 文件（从浏览器开发者工具的 **Network** 面板导出）。"
    "应用会扫描包含 `orderSearch` 的响应，提取订单并合并为一份 CSV。"
)

uploaded = st.file_uploader(
    "拖拽或选择 `.har` 文件（可多选）",
    type=["har"],
    accept_multiple_files=True,
    help="Chrome/Edge：打开开发者工具 → Network → 右键空白处 → Save all as HAR with content。"
)

run_btn = st.button("开始处理")

if run_btn:
    if not uploaded:
        st.warning("请至少上传 1 个 `.har` 文件。")
        st.stop()

    all_rows: List[Dict[str, Any]] = []
    progress = st.progress(0)

    for i, uf in enumerate(uploaded, start=1):
        with st.status(f"正在处理 **{uf.name}** …", expanded=False):
            raw = uf.read()
            har_obj = har_bytes_to_json(raw)
            if not har_obj:
                st.error(f"无法解析 HAR：{uf.name}")
            else:
                rows, _ = extract_data(har_obj)
                all_rows.extend(rows)
                st.write(f"在该文件中发现 **{len(rows)}** 条订单。")

        progress.progress(i / len(uploaded))

    if not all_rows:
        st.info("未在所上传的文件中找到订单数据。")
        st.stop()

    # 导出 CSV
    csv_bytes = to_csv_bytes(all_rows)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_name = f"orders_{ts}.csv"

    st.success(f"完成！共从 **{len(uploaded)}** 个文件提取 **{len(all_rows)}** 条订单。")
    st.download_button("⬇️ 下载合并后的 CSV", data=csv_bytes, file_name=out_name, mime="text/csv")

    # 可配置的预览区（不再显示“手机号来源统计”）
    with st.expander("预览数据（前 N 行）"):
        max_n = len(all_rows)
        default_n = min(1000, max_n)
        n = st.number_input("选择要预览的行数（从 1 开始）", min_value=1, max_value=max_n, value=default_n, step=100)
        st.dataframe(all_rows[:n], use_container_width=True)

