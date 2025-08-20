import json
import base64
import csv
import io
import re
from datetime import datetime
from typing import List, Dict, Tuple, Any

import streamlit as st


# =========================
# Core logic (adapted from your script)
# =========================

def decode_content(content):
    """Decode content with various encodings."""
    if not content:
        return None

    # If content is already a string, try to parse it as JSON
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

    # If it's base64 encoded (or could be)
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

    # If it's raw binary
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
    """Extract order information from the response data."""
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

            # Phone extraction (priority order)
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
        # Fail-soft: return whatever we collected
        pass

    return orders, phone_sources


def extract_data(har_data: Dict[str, Any]) -> Tuple[List[Dict[str, Any]], Dict[str, int]]:
    """Extract order data from HAR entries."""
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
                # Skip non-JSON content
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
            # Skip a bad entry and continue
            continue

    return extracted_data, total_phone_sources


def har_bytes_to_json(b: bytes):
    """Load and parse HAR from bytes."""
    # HAR files are JSON. Try utf-8 first, then fallbacks.
    for enc in ('utf-8', 'utf-16', 'latin1'):
        try:
            return json.loads(b.decode(enc))
        except Exception:
            continue
    # Last-ditch: try json.load on a text wrapper
    try:
        return json.loads(b)
    except Exception:
        return None


def to_csv_bytes(rows: List[Dict[str, Any]]) -> bytes:
    """Return CSV (utf-8-sig) bytes from extracted rows with Excel-safe Order ID."""
    if not rows:
        return b''

    # Zero-width space to keep Excel from scientific-notation on long IDs
    safe_rows = []
    for r in rows:
        r = dict(r)
        r['Order ID'] = '\u200B' + str(r.get('Order ID', ''))
        safe_rows.append(r)

    field_order = ['Order ID', 'Buyer Nickname', 'Status', '手机号']
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=field_order, quoting=csv.QUOTE_ALL)
    writer.writeheader()
    writer.writerows(safe_rows)
    return buf.getvalue().encode('utf-8-sig')


# =========================
# Streamlit UI
# =========================

st.set_page_config(page_title="HAR → Orders CSV", page_icon="📦", layout="centered")
st.title("📦 HAR → Orders CSV")
st.markdown(
    "Upload one or more `.har` files captured from your browser. "
    "This app will scan `orderSearch` responses, extract orders, and return one merged CSV."
)

uploaded = st.file_uploader(
    "Drop HAR file(s) here",
    type=["har"],
    accept_multiple_files=True,
    help="You can export a HAR from your browser's DevTools Network tab."
)

run_btn = st.button("Process files")

if run_btn:
    if not uploaded:
        st.warning("Please upload at least one `.har` file.")
        st.stop()

    all_rows: List[Dict[str, Any]] = []
    totals = {'recharge_data': 0, 'buyer_phone': 0, 'nickname': 0}

    progress = st.progress(0)
    for i, uf in enumerate(uploaded, start=1):
        with st.status(f"Processing **{uf.name}** …", expanded=False):
            raw = uf.read()
            har_obj = har_bytes_to_json(raw)
            if not har_obj:
                st.error(f"Could not parse HAR: {uf.name}")
            else:
                rows, phone_sources = extract_data(har_obj)
                all_rows.extend(rows)
                for k in totals:
                    totals[k] += phone_sources.get(k, 0)
                st.write(f"Found **{len(rows)}** orders in this file.")

        progress.progress(i / len(uploaded))

    if not all_rows:
        st.info("No orders found in the uploaded HAR file(s).")
        st.stop()

    # Build downloadable CSV
    csv_bytes = to_csv_bytes(all_rows)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_name = f"orders_{ts}.csv"

    st.success(f"Done! Extracted **{len(all_rows)}** orders from **{len(uploaded)}** file(s).")
    st.download_button(
        "⬇️ Download CSV",
        data=csv_bytes,
        file_name=out_name,
        mime="text/csv"
    )

    with st.expander("Phone number source breakdown"):
        st.write(
            {
                "From recharge data": totals['recharge_data'],
                "From buyer phone": totals['buyer_phone'],
                "From nickname": totals['nickname'],
            }
        )

    with st.expander("Preview first 200 rows"):
        # Streamlit can display a list of dicts directly
        st.dataframe(all_rows[:200], use_container_width=True)
